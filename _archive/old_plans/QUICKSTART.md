# Helga Quick Start Guide

## Access the Web UI
Open in your browser: `http://192.168.1.109:5000`

## Creating Your First Course
1. Go to **Courses** page
2. Enter a topic (e.g., "Ancient Rome", "Machine Learning")
3. Select depth (1-5, higher = more detailed)
4. Click **Create Course**
5. Wait 2-4 minutes for generation

## Learning Modes

### 📚 Socratic Learning (/learn)
- Interactive Q&A with AI tutor
- Toggle **Text-Only Mode** for silent learning
- Chat naturally about your course topics

### 🧠 Quiz Mode (/test)
- AI-generated questions from your courses
- Tests comprehension of concepts

### 🔄 Spaced Repetition (/review)
- FSRS-based flashcard system
- Reviews due cards automatically

### 🏰 Memory Palace (/palace)
- Navigate spatial memory loci
- Place concepts in virtual rooms

## Tips
- Use **Text-Only Mode** when you can't use audio
- Start with depth 2-3 for new topics
- Review cards daily for best retention

## Troubleshooting
- If stuck, refresh the page
- Check `docker ps` for container status
- Logs: `docker compose logs -f core-logic`

---
*Last updated: 2026-02-05*
