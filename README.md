# MPS Exams System

A lightweight web application for managing teacher registrations, attendance, and school scheduling.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the application
```bash
uvicorn main:app --reload
```

### 3. Open in your browser
```
http://localhost:8000
```

## Default Admin Account
- **Email:** admin@mps.com
- **Password:** admin123

## Pages
| URL | Description |
|-----|-------------|
| `/login` | Login page |
| `/signup` | Teacher registration |
| `/admin` | Admin panel (approve teachers, set school status) |
| `/dashboard` | Teacher dashboard |
| `/profile` | Teacher profile |
| `/progress` | Progress (Coming Soon) |
| `/test-marks` | Test Marks (Coming Soon) |

## Flow
1. Teacher signs up → status is **pending**
2. Admin logs in and **approves** the teacher
3. Teacher can now log in and use the dashboard
4. Admin can mark school as **open/closed** each day
5. Teachers can only mark attendance when school is **open**
