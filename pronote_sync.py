import datetime
import json
import os
import re
import sys
import uuid
from pathlib import Path

import truststore
truststore.inject_into_ssl()

import cv2
import numpy as np
from PIL import Image
import pronotepy

BASE_DIR = Path(__file__).parent.resolve()
CREDS_FILE = BASE_DIR / "credentials.json"
DATA_FILE = BASE_DIR / "pronote_data.json"
PLAN_FILE = BASE_DIR / "coach_plan.json"

TEST_KEYWORDS = [
    r"\bcontr[oô]le\b",
    r"\b[eé]valuation\b",
    r"\bds\b",
    r"\bdevoir surveill[eé]\b",
    r"\binterro(?:gation)?\b",
    r"\bbilan\b",
    r"\btest\b",
    r"\bqcm\b",
    r"\bexamen\b",
    r"\bexpos[eé]\b",
    r"\boral\b",
    r"\bdict[eé]e\b",
]
TEST_REGEX = re.compile("|".join(TEST_KEYWORDS), re.IGNORECASE)


def decode_qr_image(image_path: str) -> dict:
    """Decode a Pronote QR code from an image path and return the JSON payload."""
    pil_img = Image.open(image_path).convert("RGB")
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()

    # Try standard detectAndDecode
    data, _, _ = detector.detectAndDecode(img)
    if not data:
        # Try upscaling or thresholding if small/blurry
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for scale in [2.0, 0.5, 1.5]:
            resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            data, _, _ = detector.detectAndDecode(resized)
            if data:
                break
        if not data:
            _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            data, _, _ = detector.detectAndDecode(thresh)

    if not data:
        raise ValueError("Impossible de détecter ou lire le QR Code sur cette image. Assure-toi que le QR Code est bien net et entier.")

    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Le QR Code lu ne contient pas un format Pronote valide: {data[:80]}") from e


def login_with_qr(qr_data_or_image: str, pin: str, account_pin: str | None = None) -> pronotepy.Client:
    """Connect using either a QR image path or a JSON string, then save rolling credentials."""
    if os.path.exists(qr_data_or_image):
        qr_dict = decode_qr_image(qr_data_or_image)
    else:
        qr_dict = json.loads(qr_data_or_image)

    device_uuid = str(uuid.uuid4())
    client = pronotepy.Client.qrcode_login(
        qr_code=qr_dict,
        pin=str(pin).strip(),
        uuid=device_uuid,
        account_pin=account_pin,
        device_name="Antigravity Coach",
    )
    if not client.logged_in:
        raise RuntimeError("La connexion par QR code a échoué.")

    save_credentials(client)
    return client


def save_credentials(client: pronotepy.Client) -> None:
    """Save rolling token credentials to credentials.json (Pronote rotates the token on each login)."""
    creds = client.export_credentials()
    with open(CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2, ensure_ascii=False)


def get_client() -> pronotepy.Client:
    """Load saved credentials, log in via token_login, and immediately persist rotated token."""
    if not CREDS_FILE.exists():
        raise FileNotFoundError("Aucun compte Pronote configuré (credentials.json introuvable). Connecte-toi d'abord par QR Code.")

    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)

    client = pronotepy.Client.token_login(
        pronote_url=creds.get("pronote_url") or creds["url"],
        username=creds["username"],
        password=creds["password"],
        uuid=creds["uuid"],
        account_pin=creds.get("account_pin"),
        client_identifier=creds.get("client_identifier"),
        device_name="Antigravity Coach",
    )
    if not client.logged_in:
        raise RuntimeError("Le jeton Pronote a expiré. Génère un nouveau QR Code.")

    # Crucial: Pronote invalidates the previous token on each login and issues a new one!
    save_credentials(client)
    return client


def classify_homework(description: str) -> str:
    """Return 'controle' if homework description matches test/exam keywords, else 'devoir'."""
    if TEST_REGEX.search(description or ""):
        return "controle"
    return "devoir"


def compute_priority(due_date: datetime.date, item_type: str, done: bool) -> dict:
    """Compute urgency score, priority badge, and recommended start date."""
    today = datetime.date.today()
    days_left = (due_date - today).days

    if done:
        return {"level": "done", "label": "Terminé", "score": 0, "days_left": days_left}

    if item_type == "controle":
        if days_left <= 2:
            return {"level": "critical", "label": "URGENT - Contrôle imminent", "score": 100 - days_left * 5, "days_left": days_left}
        elif days_left <= 7:
            return {"level": "high", "label": "Priorité Haute - Révisions à lancer", "score": 85 - days_left * 3, "days_left": days_left}
        else:
            return {"level": "medium", "label": "Contrôle à anticiper", "score": 65 - min(days_left, 20), "days_left": days_left}
    else:
        if days_left <= 1:
            return {"level": "critical", "label": "Pour demain / Aujourd'hui !", "score": 95 - days_left * 5, "days_left": days_left}
        elif days_left <= 3:
            return {"level": "high", "label": "À faire rapidement", "score": 75 - days_left * 5, "days_left": days_left}
        else:
            return {"level": "normal", "label": "À planifier", "score": 50 - min(days_left, 20), "days_left": days_left}


def sync_all(days_ahead: int = 28) -> dict:
    """Fetch homework, lessons, grades, and student info from Pronote and save to pronote_data.json."""
    client = get_client()
    today = datetime.date.today()
    start_week = today - datetime.timedelta(days=1)
    end_date = today + datetime.timedelta(days=days_ahead)

    student_info = {
        "name": getattr(client.info, "name", "Élève"),
        "class_name": getattr(client.info, "class_name", ""),
        "establishment": getattr(client.info, "establishment", ""),
        "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

    # 1. Fetch Homework (include past 7 days for any unfinished homework + upcoming days)
    raw_homework = client.homework(date_from=today - datetime.timedelta(days=7), date_to=end_date)
    homework_list = []
    for hw in raw_homework:
        subject_name = hw.subject.name if hw.subject else "Matière"
        item_type = classify_homework(hw.description)
        prio = compute_priority(hw.date, item_type, hw.done)
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

    # Sort homework by done status (False first), then priority score DESC, then date ASC
    homework_list.sort(key=lambda x: (x["done"], -x["priority"]["score"], x["date"]))

    # 2. Fetch Timetable / Lessons (next 14 days, resilient week-by-week)
    lessons_list = []
    try:
        import pronotepy.dataClasses as dc
        user = client.parametres_utilisateur["dataSec"]["data"]["ressource"]
        first_week = client.get_week(today)
        last_week = client.get_week(today + datetime.timedelta(days=14))
        for week in range(first_week, last_week + 1):
            req_data = {
                "ressource": user,
                "avecAbsencesEleve": False,
                "avecConseilDeClasse": True,
                "estEDTPermanence": False,
                "avecAbsencesRessource": True,
                "avecDisponibilites": True,
                "avecInfosPrefsGrille": True,
                "Ressource": user,
                "NumeroSemaine": week,
                "numeroSemaine": week,
            }
            resp = client.post("PageEmploiDuTemps", 16, req_data)
            for raw_l in resp.get("dataSec", {}).get("data", {}).get("ListeCours", []):
                les = dc.Lesson(client, raw_l)
                if les.start.date() < today:
                    continue
                subj = les.subject.name if les.subject else (les.status or "Cours")
                lessons_list.append({
                    "id": str(les.id),
                    "subject": subj,
                    "start": les.start.isoformat(),
                    "end": les.end.isoformat(),
                    "classroom": les.classroom or "",
                    "teacher": les.teacher_name or "",
                    "canceled": bool(les.canceled),
                    "status": les.status or "",
                    "test": bool(getattr(les, "test", False)),
                    "background_color": les.background_color or "#3b82f6",
                })
        lessons_list.sort(key=lambda x: x["start"])
    except Exception as e:
        print(f"Note: emploi du temps non disponible via PageEmploiDuTemps ({e})", file=sys.stderr)

    # 3. Fetch Current Period Grades & Subject Averages (to know where the student needs extra help!)
    grades_list = []
    subject_averages = {}
    try:
        period = client.current_period
        if period:
            for g in period.grades:
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
                })
            for avg in getattr(period, "averages", []) or []:
                subj = avg.subject.name if avg.subject else "Général"
                subject_averages[subj] = {
                    "student": str(avg.student),
                    "class_average": str(avg.class_average),
                }
    except Exception as e:
        print(f"Note: impossible de récupérer les notes ({e})", file=sys.stderr)

    grades_list.sort(key=lambda x: x["date"], reverse=True)

    payload = {
        "student": student_info,
        "homework": homework_list,
        "lessons": lessons_list,
        "grades": grades_list[:20],
        "averages": subject_averages,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    export_markdown_summary(payload)
    return payload


def export_markdown_summary(data: dict) -> Path:
    """Export a clean Markdown brief of Pronote data for Google Drive / Gemini Gem."""
    md_path = BASE_DIR / "Mon_Pronote_Temps_Reel.md"
    student = data.get("student", {})
    lines = [
        f"# Pronote Temps Réel — {student.get('name', 'Élève')} ({student.get('class_name', '')})",
        f"- Établissement : {student.get('establishment', '')}",
        f"- Dernière synchronisation : {student.get('synced_at', '')}",
        "",
        "## Devoirs & Contrôles à venir",
    ]
    for hw in data.get("homework", []):
        status = "✅ Fait" if hw.get("done") else "⬜ À faire"
        kind = "🚨 CONTRÔLE/QCM" if hw.get("type") == "controle" else "📝 Devoir"
        lines.append(f"### {hw['date']} — {hw['subject']} ({kind} | {status})")
        lines.append(hw.get("description", "").strip())
        lines.append("")

    lines.append("## Emploi du temps & Alertes (Cours annulés / Évaluations / CCF)")
    for l in data.get("lessons", []):
        flags = []
        if l.get("canceled"):
            flags.append("❌ COURS ANNULÉ")
        if l.get("test"):
            flags.append("🚨 ÉVALUATION / CONTRÔLE")
        if l.get("status") and not l.get("canceled"):
            flags.append(f"⭐ {l['status']}")
        flag_str = f" [{' | '.join(flags)}]" if flags else ""
        lines.append(
            f"- {l['start'][:10]} {l['start'][11:16]}-{l['end'][11:16]} : **{l['subject']}** ({l.get('teacher', '')}, salle {l.get('classroom', '')}){flag_str}"
        )

    lines.append("")
    lines.append("## Moyennes actuelles (Trimestre 1)")
    for subj, val in data.get("averages", {}).items():
        lines.append(f"- **{subj}** : {val.get('student')}/20 (moyenne classe : {val.get('class_average')}/20)")

    lines.append("")
    lines.append("## Dernières notes")
    for g in data.get("grades", [])[:15]:
        cmt = f" — {g['comment']}" if g.get("comment") else ""
        lines.append(f"- {g['date']} | **{g['subject']}** : {g['grade']}/{g['out_of']} (coeff {g['coefficient']}, classe {g['average']}){cmt}")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Outil de synchronisation Pronote Coach")
    sub = parser.add_subparsers(dest="cmd")

    qr_p = sub.add_parser("qr", help="Se connecter via un QR Code (image ou JSON)")
    qr_p.add_argument("source", help="Chemin vers l'image du QR code ou chaîne JSON")
    qr_p.add_argument("pin", help="Code PIN à 4 chiffres choisi lors de la création du QR code")

    sync_p = sub.add_parser("sync", help="Synchroniser les devoirs, cours et notes")
    sync_p.add_argument("--days", type=int, default=28, help="Nombre de jours à récupérer")

    args = parser.parse_args()
    if args.cmd == "qr":
        client = login_with_qr(args.source, args.pin)
        print(f"Connecté avec succès au compte de {client.info.name} !")
        data = sync_all()
        print(f"{len(data['homework'])} devoirs/contrôles synchronisés.")
    elif args.cmd == "sync":
        data = sync_all(days_ahead=args.days)
        print(f"Synchronisation terminée pour {data['student']['name']} : {len(data['homework'])} devoirs/contrôles.")
    else:
        parser.print_help()
