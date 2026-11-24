# 🎓 Pronote Coach + Chat Gemini (Web & Mobile PWA)

Application web & mobile (PWA) qui connecte ton compte **Pronote** à un **Prof Particulier Gemini** pour :
- 📋 Prioriser tes devoirs et découper tes révisions de contrôles jour par jour
- 🗓️ Détecter automatiquement tes **cours annulés**, **évaluations** et **CCF**
- 📊 Analyser tes moyennes pour cibler les matières prioritaires
- 💬 Discuter en direct avec ton Coach IA depuis ton téléphone ou ton PC

---

## 🔒 Sécurité
Tes identifiants Pronote (`credentials.json`), tes données scolaires (`pronote_data.json`) et ta clé API Gemini (`gemini_config.json`) sont **automatiquement exclus de Git** via `.gitignore`.

---

## 🚀 Déployer gratuitement pour y accéder sur ton téléphone partout (4G/5G)

1. Crée un compte gratuit sur **[Render.com](https://render.com)** (connexion avec ton compte GitHub).
2. Clique sur **New +** → **Web Service** → choisis ton dépôt **`pronote-coach`**.
3. Configuration :
   - **Build Command :** `pip install -r requirements.txt`
   - **Start Command :** `python server.py`
4. Dans **Environment Variables**, ajoute :
   - `GEMINI_API_KEY` : ta clé gratuite [Google AI Studio](https://aistudio.google.com/apikey)
   - `PRONOTE_CREDENTIALS_JSON` : le contenu de ton fichier local `credentials.json`
5. Clique sur **Deploy** ! Ouvre le lien `https://ton-app.onrender.com` sur ton téléphone → **Partager** → **Sur l'écran d'accueil** pour l'installer comme une vraie appli.

---

## 💻 Lancer en local sur ton PC (et y accéder depuis ton téléphone en Wi-Fi)

```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python server.py
```
Ouvre ensuite `http://127.0.0.1:8765` sur ton PC (ou `http://<IP-DE-TON-PC>:8765` sur ton téléphone connecté au même Wi-Fi).
