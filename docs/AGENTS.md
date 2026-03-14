# OpenBrain Student Tutor Project

This repository implements a personal tutoring system for a student using:

- OpenBrain vector database
- Model Context Protocol (MCP)
- Vercel web interface

Primary goals:

1. Allow a student to upload study materials (PDF, DOCX, URLs)
2. Ingest those materials into OpenBrain vectors
3. Generate quizzes and flashcards
4. Provide tutoring explanations

Architecture:

Student Laptop
      ↓
Vercel Study App
      ↓
MCP Server
      ↓
OpenBrain Vector DB

Core Components:

/ingestion
Handles file ingestion and embedding

/mcp
Implements MCP server endpoints

/vercel-ui
Frontend interface for students

/vector-store
Interfaces with OpenBrain database

Supported Inputs:

- PDF
- DOCX
- TXT
- URL

Chunking Strategy:

500 tokens
100 overlap

Tutor Behavior Rules:

The AI must behave as a tutor, not an answer engine.

Rules:

1. Ask students to attempt answers before revealing solutions
2. Provide step-by-step explanations
3. Use language appropriate for a middle school student
4. Encourage effort and learning
5. Generate quizzes when possible

Endpoints to implement:

POST /ingest
POST /query
POST /generate_quiz
POST /generate_flashcards

Frontend UX Requirements:

The UI must remain extremely simple for a student.

Three buttons:

Upload Study Material
Generate Practice
Ask Tutor

Constraints:

No CLI interaction required for the student.
All actions must be available via web UI.

Future enhancements:

- student progress tracking
- weak-topic detection
- adaptive quizzes