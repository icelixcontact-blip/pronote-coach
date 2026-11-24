import http.server
import json
import os
import socketserver
import urllib.parse
from pathlib import Path

from google import genai
import pronote_sync

BASE_DIR = Path(__file__).parent.resolve()
DATA_FILE = BASE_DIR / "pronote_data.json"
COACH_FILE = BASE_DIR / "coach_tasks.json"
MD_FILE = BASE_DIR / "Mon_Pronote_Temps_Reel.md"
CONFIG_FILE = BASE_DIR / "gemini_config.json"

DEFAULT_COACH_TASKS = [
    {
        "id": "coach-esp-1",
        "day": "Aujourd'hui (Lundi 23 Nov) - URGENT POUR 12h02",
        "subject": "ESPAGNOL LV2",
        "title": "Rédiger le mail formel (80-100 mots) sur le droit à la déconnexion",
        "details": "Utiliser : 1 obligation (deber / tener que), 1 recommandation/demande (le pido que + subjonctif / es importante que + subjonctif), 1 cause (ya que / dado que), 1 conséquence (por eso / por consiguiente).",
        "duration": "25 min",
        "priority": "critical",
        "done": False,
    },
    {
        "id": "coach-esp-2",
        "day": "Aujourd'hui (Lundi 23 Nov) - URGENT POUR 12h02",
        "subject": "ESPAGNOL LV2",
        "title": "Apprendre le vocabulaire de la Séance 3 + conjugaison du subjonctif présent",
        "details": "Tu as 20/20 de moyenne en Espagnol : garde ce niveau en mémorisant les mots-clés (la desconexión digital, fuera del horario laboral, preservar la vida privada).",
        "duration": "15 min",
        "priority": "critical",
        "done": False,
    },
    {
        "id": "coach-math-test-1",
        "day": "Lundi 23 Nov (Soir)",
        "subject": "MATHEMATIQUES (M. JOUVE)",
        "title": "Préparer l'évaluation de Maths de Mardi 11h06 (test signalé sur Pronote)",
        "details": "Moyenne actuelle : 10/20 (5,5/20 au Bac Blanc). Priorité absolue : reprendre la fiche de cours de M. Jouve et refaire 2 exercices types corrigés en classe sans regarder le corrigé.",
        "duration": "45 min",
        "priority": "critical",
        "done": False,
    },
    {
        "id": "coach-math-exo",
        "day": "Mercredi 25 Nov (Après-midi libre : DST annulé !)",
        "subject": "MATHEMATIQUES",
        "title": "Terminer l'exercice 30 p. 212 (pour Vendredi 27 Nov)",
        "details": "Fais-le au calme mercredi après-midi puisque le DST de 13h56-17h54 est annulé.",
        "duration": "30 min",
        "priority": "high",
        "done": False,
    },
    {
        "id": "coach-pc-remediation",
        "day": "Mercredi 25 Nov (Après-midi libéré)",
        "subject": "PHYSIQUE-CHIMIE",
        "title": "Session rattrapage Physique-Chimie (Moyenne : 9,98/20 vs classe 13,06)",
        "details": "Faire une fiche récap des formules du chapitre en cours avec les unités et refaire le dernier TP/exercice de M. Bilas.",
        "duration": "45 min",
        "priority": "high",
        "done": False,
    },
    {
        "id": "coach-emc-qcm",
        "day": "Jeudi 26 Nov (Soir)",
        "subject": "ENS. MORAL & CIVIQUE",
        "title": "Réviser le chapitre 'Organisation des pouvoirs' + Faire le QCM Pronote",
        "details": "Pour vendredi 27 Nov à 08h56 : relire le schéma de la séparation des pouvoirs (exécutif, législatif, judiciaire) et valider le QCM sur Pronote.",
        "duration": "25 min",
        "priority": "high",
        "done": False,
    },
    {
        "id": "coach-eps-ccf",
        "day": "Jeudi 26 Nov (Soir)",
        "subject": "ED.PHYSIQUE & SPORT.",
        "title": "Préparer les affaires pour le CCF d'EPS (Vendredi 27 Nov 10h10-12h)",
        "details": "Épreuve officielle CCF (compte pour le Bac) vendredi matin de 10h10 à 11h59.",
        "duration": "10 min",
        "priority": "medium",
        "done": False,
    },
]


def load_coach_tasks():
    if not COACH_FILE.exists():
        with open(COACH_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_COACH_TASKS, f, indent=2, ensure_ascii=False)
    with open(COACH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_coach_tasks(tasks):
    with open(COACH_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)


def get_api_key() -> str:
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cfg.get("api_key", "").strip()
        except Exception:
            return ""
    return ""


def build_coach_system_instruction() -> str:
    pronote_md = MD_FILE.read_text(encoding="utf-8") if MD_FILE.exists() else "Aucune donnée Pronote."
    tasks = load_coach_tasks()
    tasks_str = "\n".join(
        f"- [{'x' if t['done'] else ' '}] ({t['day']}) {t['subject']} : {t['title']} ({t['duration']})"
        for t in tasks
    )
    return (
        "Tu es le prof particulier et coach scolaire personnel d'Ilyan (Terminale Générale TG4 au Lycée Montalembert). "
        "Tu parles en français de façon claire, motivante, structurée et concrète. "
        "Tu l'aides à prioriser ses devoirs, réviser ses contrôles, expliquer ses cours (Maths, Physique-Chimie, Philo, Espagnol, Anglais, EMC) "
        "et faire ses exercices étape par étape sans juste lui donner la réponse brute sauf s'il demande un corrigé.\n\n"
        "Voici ses données Pronote en temps réel :\n"
        f"{pronote_md}\n\n"
        "Voici sa To-Do List Coach actuelle :\n"
        f"{tasks_str}\n"
    )


class CoachHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/data":
            if not DATA_FILE.exists():
                pronote_sync.sync_all(days_ahead=60)
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["coach_tasks"] = load_coach_tasks()
            data["has_gemini_key"] = bool(get_api_key())
            self._send_json(data)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        body = json.loads(raw_body or "{}")

        if parsed.path == "/api/sync":
            try:
                data = pronote_sync.sync_all(days_ahead=60)
                data["coach_tasks"] = load_coach_tasks()
                data["has_gemini_key"] = bool(get_api_key())
                self._send_json({"ok": True, "data": data})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        if parsed.path == "/api/save_key":
            api_key = (body.get("api_key") or "").strip()
            CONFIG_FILE.write_text(json.dumps({"api_key": api_key}, indent=2), encoding="utf-8")
            self._send_json({"ok": True, "has_gemini_key": bool(api_key)})
            return

        if parsed.path == "/api/chat":
            api_key = get_api_key()
            if not api_key:
                self._send_json(
                    {"ok": False, "error": "Ajoute d'abord ta clé API Gemini gratuite (Google AI Studio) ci-dessus pour activer le chat intégré."},
                    status=400,
                )
                return

            user_msg = (body.get("message") or "").strip()
            prev_id = body.get("previous_interaction_id") or None
            if not user_msg:
                self._send_json({"ok": False, "error": "Message vide."}, status=400)
                return

            try:
                client = genai.Client(api_key=api_key)
                kwargs = {
                    "model": "gemini-4-hydrogen",
                    "input": user_msg,
                    "system_instruction": build_coach_system_instruction(),
                }
                if prev_id:
                    kwargs["previous_interaction_id"] = prev_id
                interaction = client.interactions.create(**kwargs)
                self._send_json({
                    "ok": True,
                    "reply": interaction.output_text,
                    "interaction_id": interaction.id,
                })
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        if parsed.path == "/api/toggle_coach":
            task_id = body.get("id")
            tasks = load_coach_tasks()
            for t in tasks:
                if t["id"] == task_id:
                    t["done"] = not t["done"]
            save_coach_tasks(tasks)
            self._send_json({"ok": True, "coach_tasks": tasks})
            return

        if parsed.path == "/api/toggle_hw":
            hw_id = body.get("id")
            if DATA_FILE.exists():
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for hw in data.get("homework", []):
                    if hw["id"] == hw_id:
                        hw["done"] = not hw["done"]
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                self._send_json({"ok": True, "homework": data["homework"]})
                return

        self._send_json({"ok": False, "error": "Not found"}, status=404)


if __name__ == "__main__":
    # Support cloud hosting (Render/Railway/Koyeb) via env var PRONOTE_CREDENTIALS_JSON
    env_creds = os.environ.get("PRONOTE_CREDENTIALS_JSON", "").strip()
    if env_creds and not (BASE_DIR / "credentials.json").exists():
        (BASE_DIR / "credentials.json").write_text(env_creds, encoding="utf-8")

    PORT = int(os.environ.get("PORT", "8765"))
    HOST = os.environ.get("HOST", "0.0.0.0")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), CoachHandler) as httpd:
        print(f"Pronote Coach PWA + Chat Gemini actif sur http://{HOST}:{PORT}")
        httpd.serve_forever()
