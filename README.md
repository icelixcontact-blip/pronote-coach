# 🎓 Pronote Coach IA — Web App (PWA) & App Android (.apk)

Application **multi-utilisateurs** (Web App installable iPhone/Android + APK Android natif via Capacitor) qui permet à n'importe quel élève de scanner son **QR Code Pronote** pour obtenir :
- 📋 **Super To-Do List intelligente** (générée automatiquement à partir des devoirs, évaluations EDT et matières à remonter)
- 🗓️ **Emploi du temps & Alertes** (cours annulés, évaluations cachées dans l'EDT, CCF)
- 📊 **Diagnostic des moyennes**
- 💬 **Prof Particulier IA Gemini** connecté en direct aux données Pronote de l'élève

---

## 🌐 1. Mettre la Web App en ligne gratuitement (Render.com)

1. Connecte-toi sur **[render.com](https://render.com)** avec ton compte GitHub.
2. Clique sur **New +** → **Blueprint** (ou **Web Service**) → sélectionne **`pronote-coach`**.
3. *(Optionnel)* Ajoute la variable d'environnement `GEMINI_API_KEY` (clé gratuite [Google AI Studio](https://aistudio.google.com/apikey)) pour que tes potes n'aient même pas besoin d'entrer leur propre clé !
4. Déploie → partage le lien `https://pronote-coach.onrender.com` à tes potes ! Chacun scanne son propre QR Code Pronote dans son espace isolé.

---

## 📱 2. Télécharger l'application Android (`.apk`) automatiquement

À chaque push sur `main`, **GitHub Actions** compile automatiquement le fichier **`.apk` Android** (via Capacitor) !
1. Ouvre l'onglet **Actions** de ton dépôt GitHub.
2. Clique sur le workflow **Build Android APK (Pronote Coach)** (coche verte ✅).
3. En bas de la page, télécharge **`PronoteCoach-Android-APK`** et installe-le sur ton téléphone Android !
4. À l'ouverture de l'APK, déroule **⚙️ Serveur API** sur l'écran de connexion et colle l'URL de ton serveur Render (`https://pronote-coach-xxxx.onrender.com`).

---

## 💻 3. Lancer en local sur PC

```bash
.\.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8765
```
Ouvre ensuite `http://127.0.0.1:8765` (ou `http://<IP-DE-TON-PC>:8765` depuis ton téléphone en Wi-Fi).
