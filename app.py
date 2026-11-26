import datetime
import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import truststore
truststore.inject_into_ssl()

import pronotepy
import pronotepy.dataClasses as dc
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel

import pronote_sync

BASE_DIR = Path(__file__).parent.resolve()
WWW_DIR = BASE_DIR / "www"
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
WWW_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Pronote Coach Multi-User API", version="2.0.0")

# Allow Capacitor Android/iOS WebView & PWA origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _session_dir(token: str) -> Path:
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise HTTPException(status_code=401, detail="Session invalide ou expirée. Reconnecte-toi avec ton QR Code Pronote.")
    sdir = SESSIONS_DIR / token
    if not sdir.exists():
        raise HTTPException(status_code=401, detail="Session introuvable. Reconnecte-toi avec ton QR Code Pronote.")
    return sdir


def _save_session_creds(sdir: Path, client: pronotepy.Client) -> None:
    creds = client.export_credentials()
    (sdir / "credentials.json").write_text(json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_session_client(sdir: Path) -> pronotepy.Client:
    creds_path = sdir / "credentials.json"
    if not creds_path.exists():
        raise HTTPException(status_code=401, detail="Identifiants Pronote manquants. Reconnecte-toi par QR Code.")
    creds = json.loads(creds_path.read_text(encoding="utf-8"))
    client = pronotepy.Client.token_login(
        pronote_url=creds.get("pronote_url") or creds["url"],
        username=creds["username"],
        password=creds["password"],
        uuid=creds["uuid"],
        account_pin=creds.get("account_pin"),
        client_identifier=creds.get("client_identifier"),
        device_name="Pronote Coach App",
    )
    if not client.logged_in:
        raise HTTPException(status_code=401, detail="Jeton Pronote expiré. Génère un nouveau QR Code Pronote.")
    _save_session_creds(sdir, client)
    return client


def _build_auto_coach_tasks(data: dict, existing_tasks: list) -> list:
    """Auto-generate smart prioritized coach tasks from any student's Pronote data, preserving done states."""
    done_map = {t["id"]: t.get("done", False) for t in existing_tasks}
    custom_tasks = [t for t in existing_tasks if t.get("custom")]
    generated = []
    today = datetime.date.today()

    # 1. Upcoming tests detected in timetable (even if not in Homework tab!)
    for les in data.get("lessons", []):
        if les.get("canceled"):
            continue
        if les.get("test") or (les.get("status") and "CCF" in les.get("status", "").upper()):
            tid = f"auto-les-{les['id']}"
            dt = les["start"][:10]
            hh = les["start"][11:16]
            label = "CCF / Épreuve Bac" if "CCF" in (les.get("status") or "").upper() else "Évaluation signalée sur l'EDT"
            generated.append({
                "id": tid,
                "day": f"Pour le {dt} à {hh}",
                "subject": les["subject"],
                "title": f"Réviser : {label} ({les['subject']})",
                "details": f"Prof : {les.get('teacher') or '—'} • Salle {les.get('classroom') or '—'}. Relis le cours et refais 2 exercices types sans regarder la correction.",
                "duration": "45 min",
                "priority": "critical",
                "done": done_map.get(tid, False),
                "custom": False,
            })

    # 2. Upcoming homework / tests
    for hw in data.get("homework", []):
        due = datetime.date.fromisoformat(hw["date"])
        days_left = (due - today).days
        if days_left < -1:
            continue
        tid = f"auto-hw-{hw['id']}"
        is_test = hw.get("type") == "controle"
        prio = "critical" if days_left <= 1 or (is_test and days_left <= 3) else ("high" if days_left <= 4 or is_test else "normal")
        when = "Aujourd'hui !" if days_left <= 0 else ("Pour demain !" if days_left == 1 else f"Dans {days_left} jours ({hw['date']})")
        short_desc = (hw.get("description") or "").strip()
        if len(short_desc) > 220:
            short_desc = short_desc[:217] + "..."
        generated.append({
            "id": tid,
            "day": when,
            "subject": hw["subject"],
            "title": f"{'🚨 Réviser contrôle' if is_test else '📝 Faire devoir'} — {hw['subject']}",
            "details": short_desc,
            "duration": "40 min" if is_test else "25 min",
            "priority": prio,
            "done": done_map.get(tid, hw.get("done", False)),
            "custom": False,
        })

    # 3. Coaching recommendation for weakest subjects (< 11/20)
    for subj, avg in data.get("averages", {}).items():
        try:
            score = float(str(avg.get("student", "")).replace(",", "."))
        except ValueError:
            continue
        if score < 10.5:
            tid = f"auto-rem-{re.sub(r'[^a-z0-9]', '', subj.lower())}"
            generated.append({
                "id": tid,
                "day": "Objectif Remontada (Cette semaine)",
                "subject": subj,
                "title": f"Session rattrapage {subj} (Moyenne actuelle : {avg['student']}/20)",
                "details": f"Moyenne de classe : {avg.get('class_average', '?')}/20. Fais une fiche-mémo des formules/méthodes du chapitre actuel.",
                "duration": "35 min",
                "priority": "high",
                "done": done_map.get(tid, False),
                "custom": False,
            })

    return custom_tasks + generated


def _sync_session_data(sdir: Path, client: pronotepy.Client, days_ahead: int = 45) -> dict:
    today = datetime.date.today()
    end_date = today + datetime.timedelta(days=days_ahead)

    student_info = {
        "name": getattr(client.info, "name", "Élève"),
        "class_name": getattr(client.info, "class_name", ""),
        "establishment": getattr(client.info, "establishment", ""),
        "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

    # Homework
    raw_homework = client.homework(date_from=today - datetime.timedelta(days=3), date_to=end_date)
    homework_list = []
    for hw in raw_homework:
        subject_name = hw.subject.name if hw.subject else "Matière"
        item_type = pronote_sync.classify_homework(hw.description)
        prio = pronote_sync.compute_priority(hw.date, item_type, hw.done)
        files = [{"name": f.name, "url": f.url} for f in (hw.files or [])]
        homework_list.append({
            "id": str(hw.id),
            "subject": subject_name,
            "date": hw.date.isoformat(),
            "description": hw.description,
            "done": bool(hw.done),
            "type": item_type,
            "background_color": hw.background_color or "#6366f1",
            "files": files,
            "priority": prio,
        })
    homework_list.sort(key=lambda x: (x["done"], -x["priority"]["score"], x["date"]))

    # Lessons (full current week Monday->Sunday + next week, resilient week-by-week)
    # Also cross-reference homework tests/contrôles on the same day & subject so they light up in RED!
    hw_tests_by_day_subj = set()
    for hw in homework_list:
        if hw.get("type") == "controle":
            hw_tests_by_day_subj.add((hw["date"], hw["subject"].strip().upper()))

    lessons_list = []
    try:
        user = client.parametres_utilisateur["dataSec"]["data"]["ressource"]
        monday_this_week = today - datetime.timedelta(days=today.weekday())
        first_week = client.get_week(monday_this_week)
        last_week = client.get_week(monday_this_week + datetime.timedelta(days=13))
        seen_ids = set()
        for week in range(first_week, last_week + 1):
            req_data = {
                "ressource": user,
                "Ressource": user,
                "avecAbsencesEleve": False,
                "avecConseilDeClasse": True,
                "estEDTPermanence": False,
                "avecAbsencesRessource": True,
                "avecDisponibilites": True,
                "avecInfosPrefsGrille": True,
                "NumeroSemaine": week,
                "numeroSemaine": week,
            }
            resp = client.post("PageEmploiDuTemps", 16, req_data)
            for raw_l in resp.get("dataSec", {}).get("data", {}).get("ListeCours", []):
                les = dc.Lesson(client, raw_l)
                if les.start.date() < monday_this_week:
                    continue
                lid = f"{les.id}_{les.start.isoformat()}"
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)
                subj = les.subject.name if les.subject else (les.status or "Cours")
                les_date = les.start.date().isoformat()
                status_str = les.status or ""
                is_eval = (
                    bool(getattr(les, "test", False))
                    or ("CCF" in status_str.upper())
                    or ("DST" in subj.upper())
                    or ("DEVOIR SUR TABLE" in subj.upper())
                    or ("BAC BLANC" in status_str.upper())
                    or ((les_date, subj.strip().upper()) in hw_tests_by_day_subj)
                )
                lessons_list.append({
                    "id": str(les.id),
                    "subject": subj,
                    "start": les.start.isoformat(),
                    "end": les.end.isoformat(),
                    "classroom": les.classroom or "",
                    "teacher": les.teacher_name or "",
                    "canceled": bool(les.canceled),
                    "status": status_str,
                    "test": bool(is_eval and not les.canceled),
                    "background_color": les.background_color or "#3b82f6",
                })
        lessons_list.sort(key=lambda x: x["start"])
    except Exception:
        pass

    # Grades & Averages (use current period, or fallback to latest period with grades when a new trimester starts)
    grades_list = []
    subject_averages = {}
    active_period_name = "Trimestre en cours"
    try:
        periods_to_check = []
        if client.current_period:
            periods_to_check.append(client.current_period)
        for p in getattr(client, "periods", []) or []:
            if p not in periods_to_check and getattr(p, "grades", None):
                periods_to_check.append(p)

        chosen_period = None
        for p in periods_to_check:
            if getattr(p, "grades", None):
                chosen_period = p
                break

        if chosen_period:
            active_period_name = getattr(chosen_period, "name", "Période")
            for g in chosen_period.grades:
                subj = g.subject.name if g.subject else "Autre"
                grades_list.append({
                    "id": str(g.id),
                    "subject": subj,
                    "grade": str(g.grade),
                    "out_of": str(g.out_of),
                    "date": g.date.isoformat() if g.date else "",
                    "comment": g.comment or "",
                    "coefficient": str(g.coefficient),
                    "average": str(g.average or ""),
                    "period": active_period_name,
                })
            for avg in getattr(chosen_period, "averages", []) or []:
                subj = avg.subject.name if avg.subject else "Général"
                subject_averages[subj] = {
                    "student": str(avg.student),
                    "class_average": str(avg.class_average),
                }
    except Exception:
        pass
    grades_list.sort(key=lambda x: x["date"], reverse=True)

    payload = {
        "student": student_info,
        "period_name": active_period_name,
        "homework": homework_list,
        "lessons": lessons_list,
        "grades": grades_list,
        "averages": subject_averages,
    }
    (sdir / "pronote_data.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    tasks_path = sdir / "coach_tasks.json"
    existing_tasks = json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else []
    new_tasks = _build_auto_coach_tasks(payload, existing_tasks)
    tasks_path.write_text(json.dumps(new_tasks, indent=2, ensure_ascii=False), encoding="utf-8")

    return payload


def _get_gemini_key(sdir: Path) -> str:
    # 1. Per-user key if provided
    cfg_path = sdir / "gemini_config.json"
    if cfg_path.exists():
        try:
            k = json.loads(cfg_path.read_text(encoding="utf-8")).get("api_key", "").strip()
            if k:
                return k
        except Exception:
            pass
    # 2. Server-wide shared key
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _build_system_instruction(sdir: Path) -> str:
    data = json.loads((sdir / "pronote_data.json").read_text(encoding="utf-8"))
    tasks = json.loads((sdir / "coach_tasks.json").read_text(encoding="utf-8")) if (sdir / "coach_tasks.json").exists() else []
    st = data.get("student", {})
    hw_txt = "\n".join(
        f"- {h['date']} | {h['subject']} ({'CONTROLE' if h['type']=='controle' else 'Devoir'}, {'Fait' if h['done'] else 'A faire'}): {h['description']}"
        for h in data.get("homework", [])
    )
    les_txt = "\n".join(
        f"- {l['start'][:10]} {l['start'][11:16]}-{l['end'][11:16]} | {l['subject']} ({l['teacher']}){' [ANNULE]' if l['canceled'] else ''}{' [EVALUATION]' if l['test'] else ''}{(' ['+l['status']+']') if l['status'] and not l['canceled'] else ''}"
        for l in data.get("lessons", [])[:25]
    )
    avg_txt = "\n".join(
        f"- {subj}: {v['student']}/20 (classe {v['class_average']}/20)"
        for subj, v in data.get("averages", {}).items()
    )
    grades_txt = "\n".join(
        f"- {g['date']} | {g['subject']}: {g['grade']}/{g['out_of']} (coeff {g['coefficient']}, classe {g['average']}){' - ' + g['comment'] if g.get('comment') else ''}"
        for g in data.get("grades", [])[:25]
    )
    tasks_txt = "\n".join(
        f"- [{'x' if t['done'] else ' '}] {t['day']} | {t['subject']}: {t['title']}"
        for t in tasks
    )
    return (
        f"Tu es le prof particulier et coach scolaire personnel de {st.get('name', 'cet élève')} "
        f"(classe {st.get('class_name', '')}, {st.get('establishment', '')}). "
        "Tu réponds en français de manière claire, motivante, structurée et concrète. "
        "Tu l'aides à prioriser ses devoirs, découper ses révisions avant les contrôles, comprendre ses cours et faire des quiz.\n\n"
        f"DATE DU JOUR : {datetime.date.today().isoformat()}\n\n"
        f"DEVOIRS & CONTROLES :\n{hw_txt or 'Aucun'}\n\n"
        f"EMPLOI DU TEMPS :\n{les_txt or 'Non disponible'}\n\n"
        f"MOYENNES ({data.get('period_name', 'Trimestre')}) :\n{avg_txt or 'Non disponibles'}\n\n"
        f"DERNIERES NOTES :\n{grades_txt or 'Aucune'}\n\n"
        f"TO-DO LIST ACTUELLE :\n{tasks_txt or 'Vide'}\n"
    )


# ---------- Pydantic Models ----------
class QrJsonLoginReq(BaseModel):
    qr_json: str
    pin: str


class ToggleReq(BaseModel):
    id: str
    kind: str = "coach"  # "coach" or "homework"


class AddTaskReq(BaseModel):
    subject: str
    title: str
    day: str = "À faire"
    duration: str = "20 min"


class ChatReq(BaseModel):
    message: str
    previous_interaction_id: Optional[str] = None


class ApiKeyReq(BaseModel):
    api_key: str


# ---------- Health Check (for Keep-Alive ping) ----------
@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Auth Routes (Multi-User) ----------
@app.post("/api/auth/qr-json")
def auth_qr_json(req: QrJsonLoginReq):
    try:
        qr_dict = json.loads(req.qr_json)
        device_uuid = str(uuid.uuid4())
        client = pronotepy.Client.qrcode_login(
            qr_code=qr_dict,
            pin=req.pin.strip(),
            uuid=device_uuid,
            device_name="Pronote Coach App",
        )
        if not client.logged_in:
            raise HTTPException(status_code=400, detail="Connexion Pronote refusée. Vérifie ton code PIN à 4 chiffres et que le QR code n'a pas déjà été utilisé.")
        token = secrets.token_urlsafe(32)
        sdir = SESSIONS_DIR / token
        sdir.mkdir(parents=True, exist_ok=True)
        _save_session_creds(sdir, client)
        data = _sync_session_data(sdir, client)
        return {"ok": True, "token": token, "student": data["student"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Impossible de se connecter à Pronote : {e}")


@app.post("/api/auth/qr-image")
async def auth_qr_image(pin: str = Form(...), file: UploadFile = File(...)):
    tmp_path = None
    try:
        suffix = Path(file.filename or "qr.png").suffix or ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        qr_dict = pronote_sync.decode_qr_image(tmp_path)
        device_uuid = str(uuid.uuid4())
        client = pronotepy.Client.qrcode_login(
            qr_code=qr_dict,
            pin=pin.strip(),
            uuid=device_uuid,
            device_name="Pronote Coach App",
        )
        if not client.logged_in:
            raise HTTPException(status_code=400, detail="Connexion Pronote refusée. Vérifie le code PIN à 4 chiffres.")
        token = secrets.token_urlsafe(32)
        sdir = SESSIONS_DIR / token
        sdir.mkdir(parents=True, exist_ok=True)
        _save_session_creds(sdir, client)
        data = _sync_session_data(sdir, client)
        return {"ok": True, "token": token, "student": data["student"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur QR Code : {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/logout")
def logout(x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    shutil.rmtree(sdir, ignore_errors=True)
    return {"ok": True}


# ---------- Student Data & Sync Routes ----------
@app.get("/api/me")
def get_me(x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    data = json.loads((sdir / "pronote_data.json").read_text(encoding="utf-8"))
    tasks = json.loads((sdir / "coach_tasks.json").read_text(encoding="utf-8")) if (sdir / "coach_tasks.json").exists() else []
    data["coach_tasks"] = tasks
    data["has_gemini_key"] = bool(_get_gemini_key(sdir))
    return data


@app.post("/api/sync")
def sync_me(x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    client = _get_session_client(sdir)
    data = _sync_session_data(sdir, client)
    tasks = json.loads((sdir / "coach_tasks.json").read_text(encoding="utf-8"))
    data["coach_tasks"] = tasks
    data["has_gemini_key"] = bool(_get_gemini_key(sdir))
    return {"ok": True, "data": data}


@app.post("/api/tasks/toggle")
def toggle_task(req: ToggleReq, x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    if req.kind == "coach":
        p = sdir / "coach_tasks.json"
        tasks = json.loads(p.read_text(encoding="utf-8"))
        for t in tasks:
            if t["id"] == req.id:
                t["done"] = not t["done"]
        p.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}
    else:
        p = sdir / "pronote_data.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for h in data.get("homework", []):
            if h["id"] == req.id:
                h["done"] = not h["done"]
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}


@app.post("/api/tasks/add")
def add_task(req: AddTaskReq, x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    p = sdir / "coach_tasks.json"
    tasks = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    tasks.insert(0, {
        "id": f"custom-{secrets.token_hex(4)}",
        "day": req.day,
        "subject": req.subject.upper(),
        "title": req.title,
        "details": "Tâche ajoutée manuellement.",
        "duration": req.duration,
        "priority": "high",
        "done": False,
        "custom": True,
    })
    p.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "coach_tasks": tasks}


@app.post("/api/settings/api-key")
def save_api_key(req: ApiKeyReq, x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    (sdir / "gemini_config.json").write_text(json.dumps({"api_key": req.api_key.strip()}), encoding="utf-8")
    return {"ok": True, "has_gemini_key": bool(req.api_key.strip())}


@app.post("/api/chat")
def chat_with_coach(req: ChatReq, x_session_token: str = Header(default="")):
    sdir = _session_dir(x_session_token)
    api_key = _get_gemini_key(sdir)
    if not api_key:
        raise HTTPException(status_code=400, detail="Ajoute ta clé API Gemini gratuite (Google AI Studio) pour activer le chat.")
    try:
        client = genai.Client(api_key=api_key)
        kwargs = {
            "model": "gemini-4-hydrogen",
            "input": req.message.strip(),
            "system_instruction": _build_system_instruction(sdir),
        }
        if req.previous_interaction_id:
            kwargs["previous_interaction_id"] = req.previous_interaction_id
        interaction = client.interactions.create(**kwargs)
        return {"ok": True, "reply": interaction.output_text, "interaction_id": interaction.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur Gemini : {e}")


# ---------- Serve Static PWA Frontend ----------
@app.get("/")
def serve_index():
    return FileResponse(WWW_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(WWW_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
