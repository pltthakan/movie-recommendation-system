# 🎬 Movie Discovery & Personalized Recommendation System (Web App)

**Author:** Hakan Polat
**Project Name:** BitirmeProjesi
**Tech Stack:** Flask + PostgreSQL + TMDB API + Embedding-based Recommendation
**Deployment:** Docker Compose (web + db)

---

## Table of Contents

1. Project Overview
2. Problem Statement & Motivation
3. Goals & Success Criteria
4. Scope & Assumptions
5. Technologies Used
6. Architecture Overview
7. Modules & Layers
8. Database Design
9. REST API Design & Usage
10. How the Recommendation System Works
11. Caching & Performance Strategy
12. Logging & Observability
13. Security & Authentication
14. Testing (Selenium + Pytest)
15. Setup & Run
16. Conclusion

---

# 1) Project Overview

This project is a web application that helps users discover movies and provides **personalized recommendations** based on user interactions.

The system retrieves movie data from TMDB (The Movie Database), manages user authentication, collects interaction signals (favorites, ratings, trailer views), and generates recommendations using an embedding-based approach.

### Key Features

* Movie search & listing
* Movie detail page
* Add to favorites
* Rating (like/dislike)
* Trailer viewing event tracking
* **Personalized recommendations (embedding-based)**
* Dockerized setup
* Logging & UI testing

---

# 2) Problem Statement & Motivation

With the increasing amount of content available today, users struggle to choose what to watch.

This project addresses:

* **Discovery problem:** Difficulty finding relevant movies
* **Lack of personalization:** Same content shown to all users
* **Underutilized user signals:** Interactions like favorites and ratings are not leveraged

**Motivation:** Even small user interactions (favorites, ratings, trailer views) can significantly improve recommendation quality.

---

# 3) Goals & Success Criteria

## Goals

* Enable users to search and view movie details
* Store user interactions (favorites, ratings, trailer views)
* Generate personalized recommendations
* Run the system with a single Docker command
* Track behavior via logging
* Validate functionality with UI tests

## Success Criteria

* Core pages (`/`, `/search`, `/detail/<id>`) work correctly
* Authentication flow works (register/login)
* `/api/personalized` returns recommendations when data exists
* Logs capture requests and key events
* UI tests pass successfully

---

# 4) Scope & Assumptions

## Included

* Web UI (Flask templates + JS)
* PostgreSQL database
* TMDB API integration
* Embedding-based recommendation system
* Cache (in-memory + Redis-ready design)
* Logging system
* Selenium-based UI testing

---

# 5) Technologies Used

## Backend

* Python 3.11+
* Flask
* Gunicorn (production-ready WSGI server)
* PostgreSQL (psycopg)
* requests (for TMDB API)

## Recommendation System

* sentence-transformers / transformers
* NumPy

## Frontend

* Jinja2 templates
* Vanilla JavaScript
* TailwindCSS (CDN)

## DevOps

* Docker / Docker Compose
* Redis (optional, for caching)

## Testing

* pytest
* selenium
* webdriver-manager

---

# 6) Architecture Overview

The project is designed as a **modular monolith**:

* `app/__init__.py` → App initialization, blueprint registration, logging setup
* `app/blueprints/` → UI pages, authentication, API endpoints
* `app/services/` → TMDB, recommendation engine, embeddings, utilities
* `app/db.py` → Database connection & initialization
* `docker-compose.yml` → Services (web + db + optional redis)
* `wsgi.py` → Entry point for Gunicorn

### High-Level Flow

1. User sends a request from UI
2. Flask route handles it
3. Service layer processes logic (TMDB / DB / embeddings)
4. Response returned as HTML or JSON
5. Logs are recorded

---

# 7) Modules & Layers

## Blueprints

* **pages** → UI pages (home, search, detail, favorites)
* **auth** → register / login / logout
* **api** → `/api/*` endpoints

## Services

* `tmdb.py` → TMDB API integration & caching
* `recommender.py` → candidate pool, scoring, recommendation generation
* `embeddings.py` → embedding generation
* `events.py` → user event logging
* `auth.py` → authentication utilities
* `utils.py` → helper functions

---

# 8) Database Design (Summary)

The system stores user interactions to generate recommendations.

Key tables:

* `users`
* `favorites`
* `ratings`
* `trailer_events`
* `candidate_movies`
* `user_profiles`
* `user_recommendations`

This design reduces recomputation cost and enables efficient recommendation generation.

---

# 9) REST API Design & Usage

The project uses **REST APIs** to dynamically update UI components.

### Example Endpoints

* `GET /api/featured` → returns popular/featured movies
* `GET /api/discover` → filtered movie discovery
* `GET /api/search_suggest` → autocomplete suggestions
* `POST /api/trailer_event` → logs trailer view
* `GET /api/personalized` → personalized recommendations

These endpoints are consumed via JavaScript without page reload.

---

# 10) How the Recommendation System Works

The pipeline:

### 1. User Signals

* Favorites (strong positive signal)
* Ratings (positive/negative signal)
* Trailer views (medium signal)

### 2. User Profile (Embedding)

* Combine embeddings of interacted movies
* Weighted average → normalized vector
* Stored in `user_profiles`

### 3. Candidate Pool

* Movies fetched from TMDB (popular, trending, etc.)
* Stored in `candidate_movies`
* Embedding matrix prepared

### 4. Scoring

* Score = cosine similarity (dot product)
* Already seen movies are filtered out
* Top-N results returned
* Cached in `user_recommendations`

---

# 11) Caching & Performance Strategy

* **@lru_cache** → for static data (genres, etc.)
* **In-memory cache** → candidate embeddings stored in RAM
* **Redis (design-ready)** → for scalable caching in production

---

# 12) Logging & Observability

Centralized logging is implemented.

### Example Logs

* `/api/featured called`
* `Login SUCCESS / FAILED`
* `Register FAILED (duplicate)`
* `/api/personalized → from_cache / fresh`

### Benefits

* Easier debugging
* Monitoring system behavior
* Demonstrates production-readiness

Logs can be viewed via:

```bash
docker compose logs -f web
```

---

# 13) Security & Authentication

* Passwords are **hashed**
* Session-based authentication
* Protected endpoints via `login_required`
* Sensitive data can be masked or hashed in logs

---

# 14) Testing (Selenium + Pytest)

End-to-end UI tests are implemented.

### What is tested?

* Homepage loading
* Search functionality

### Run tests:

```bash
export APP_BASE_URL=http://localhost:5002
pytest -q
```

Expected result:

```
2 passed
```

---

# 15) Setup & Run

## Run with Docker

```bash
docker compose up --build
```

### Ports

* Web: [http://localhost:5002](http://localhost:5002)
* DB: localhost:5433

## View Logs

```bash
docker compose logs -f web
```

## Run Tests (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install selenium webdriver-manager pytest

export APP_BASE_URL=http://localhost:5002
pytest -q
```

---

# 16) Conclusion

This project demonstrates a full-stack web application that solves the movie discovery problem using an embedding-based recommendation system.

With Dockerized deployment, logging, and testing, the project goes beyond a typical graduation project and approaches real-world production standards.

---
