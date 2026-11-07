# Unified Educational Platform: Comprehensive User & Administrator Manual

This document provides the authoritative, production-grade operating manual for the **Unified Educational Platform**, encompassing the **Student Telegram Collector Bot**, the **Teaching Assistant Control Hub (Telegram Mini App)**, and the **Grade Flow Proportional Grading Web Application**.

---

## Table of Contents
1. [System Architecture & Security Philosophy](#1-system-architecture--security-philosophy)
2. [Part 1: Student Guide (Telegram Collector Bot)](#2-part-1-student-guide-telegram-collector-bot)
   - [2.1 First-Time Onboarding & Registration](#21-first-time-onboarding--registration)
   - [2.2 Two-Stage Password Authentication Protocol](#22-two-stage-password-authentication-protocol)
   - [2.3 Navigating the Persistent Keyboard & Homework Dock](#23-navigating-the-persistent-keyboard--homework-dock)
   - [2.4 Submitting Homework Deliverables (HW 1–5)](#24-submitting-homework-deliverables-hw-15)
   - [2.5 Editing, Resubmitting & Quota Deduplication](#25-editing-resubmitting--quota-deduplication)
   - [2.6 Checking Submission & Grading Status](#26-checking-submission--grading-status)
   - [2.7 Cancelling Operations](#27-cancelling-operations)
   - [2.8 Security Lockouts & Resolution Procedure](#28-security-lockouts--resolution-procedure)
3. [Part 2: Teaching Assistant & Instructor Admin Guide](#3-part-2-teaching-assistant--instructor-admin-guide)
   - [3.1 Accessing the Telegram Mini App (TMA) Control Hub](#31-accessing-the-telegram-mini-app-tma-control-hub)
   - [3.2 Tab 1: Live Roster & Submission Matrix](#32-tab-1-live-roster--submission-matrix)
   - [3.3 Tab 2: Broadcast Communication Console](#33-tab-2-broadcast-communication-console)
   - [3.4 Tab 3: Unified Student Access & Account Control](#34-tab-3-unified-student-access--account-control)
   - [3.5 Telegram Chat In-Line Admin Commands](#35-telegram-chat-in-line-admin-commands)
4. [Part 3: Grade Flow Web Platform & Supabase Sync](#4-part-3-grade-flow-web-platform--supabase-sync)
   - [4.1 Gradebook Navigation & 2D Matrix Grid](#41-gradebook-navigation--2d-matrix-grid)
   - [4.2 Proportional Normalized Grading Engine](#42-proportional-normalized-grading-engine)
   - [4.3 Database Architecture & Supabase Synchronization](#43-database-architecture--supabase-synchronization)
   - [4.4 Roster Ingestion & Export Protocol](#44-roster-ingestion--export-protocol)
5. [Part 4: Production Deployment & 1-Step Rollback Reference](#5-part-4-production-deployment--1-step-rollback-reference)
   - [5.1 Environment Configuration & Secrets Management](#51-environment-configuration--secrets-management)
   - [5.2 Vercel Serverless Function & Webhook Setup](#52-vercel-serverless-function--webhook-setup)
   - [5.3 Verification & Automated Test Suites](#53-verification--automated-test-suites)
   - [5.4 Authoritative Disaster Recovery & Rollback Matrix](#54-authoritative-disaster-recovery--rollback-matrix)

---

## 1. System Architecture & Security Philosophy

The Unified Educational Platform eliminates manual data entry, fragmented spreadsheets, and scattered email attachments by integrating three core tiers:

```
[Student Telegram Clients]
          │
          ▼
[Telegram Homework Collector Bot] ──(Webhooks / Long-Polling)──┐
          │                                                    │
          ▼                                                    ▼
[Supergroup Forum Topics Archive]                     [Supabase PostgreSQL]
                                                      (Shared Source of Truth)
                                                               │
          ┌────────────────────────────────────────────────────┤
          ▼                                                    ▼
[TMA TA Control Hub (/admin)]                         [Grade Flow Web App]
(Mobile & Desktop Telegram Mini App)                  (Proportional Grading Engine)
```

### Core Tenets
1. **Zero-Trust Identity Binding:** Every interaction in the Telegram bot is verified against the caller's verified numeric `chat_id`. Students cannot submit assignments or inspect records on behalf of classmates.
2. **Immutable Submission Indexing:** Student deliverables are keyed by the academic `student_id`. If an account unbinds or changes Telegram accounts, historical submissions, edit quotas, and grading records remain 100% intact.
3. **Surgical Administrative Remediation:** Teaching Assistants have separate controls for unlocking accounts (`[Unblock Lockout]`) versus unlinking Telegram bindings (`[Reset Session]`), guaranteeing zero accidental data loss.

---

## 2. Part 1: Student Guide (Telegram Collector Bot)

### 2.1 First-Time Onboarding & Registration
1. Open the course Telegram bot in the official Telegram client.
2. Click **Start** or send the command:
   ```text
   /start
   ```
3. The bot greets the user and requests their official **Academic Student ID** (e.g., `4002019015`).
4. Enter your Student ID as plain text:
   - The bot verifies that the ID exists in the approved course roster.
   - If the ID is already claimed by another active Telegram account, registration is refused immediately. Contact your course TA if you switched Telegram accounts.

### 2.2 Two-Stage Password Authentication Protocol
1. Upon entering an approved Student ID, the bot enters password verification state and prompts for the personal password.
2. Enter your unique 10-character password issued by the course staff:
   - **Case-Sensitive:** Passwords must match the roster credential file exactly.
   - **Remaining Attempts:** The bot permits up to **5 failed password attempts** before enforcing a security lockout. With each failed attempt, a warning displays remaining attempts (e.g., `3 attempt(s) remaining`).
3. Upon entering the correct password:
   - The Telegram `chat_id` is atomically registered in the `students` table.
   - The student receives an onboarding confirmation message.
   - The persistent custom homework reply keyboard appears on the student's screen.

### 2.3 Navigating the Persistent Keyboard & Homework Dock
Once registered, the bot displays a persistent reply keyboard:

```
┌────────────────────────────────────────┐
│  HW 1  │  HW 2  │  HW 3  │  HW 4  │  HW 5 │
├────────────────────────────────────────┤
│     📊 Status     │      ❌ Cancel     │
└────────────────────────────────────────┘
```

- **HW 1 to HW 5:** Selects the target assignment slot for file upload.
- **Status:** Displays personal submission status, file names, timestamps, and graded scores.
- **Cancel:** Resets the current selection mode back to the default state.

### 2.4 Submitting Homework Deliverables (HW 1–5)
1. Tap the button for the assignment you wish to submit (e.g., **HW 1**).
2. The bot highlights the active selection:
   ```text
   Selected: HW 1.
   Please upload your homework deliverable as a single PDF document (.pdf).
   ```
3. Attach and send your file as a **Document** (not as a photo or compressed image).
   - **Accepted Format:** `.pdf` only. Non-PDF files (e.g., `.docx`, `.png`, `.zip`) are rejected with an explicit error.
   - **File Size:** Under Telegram bot document limits (up to 20 MB recommended).
4. The bot processes the document:
   - Verifies PDF magic bytes (`%PDF-`).
   - Forwards the deliverable to the TA Supergroup Forum topic designated for that specific homework slot.
   - Inserts or updates the submission record in the shared database.
5. A **Verification Receipt** is returned to the student:
   ```text
   ✅ Homework 1 Submission Confirmed
   • Student ID: 4002019015
   • File: HW1_4002019015.pdf
   • Submission Time: 2026-11-07 14:15:22 Tehran Time
   • Edit Count: 0 / 2 edits used
   ```

### 2.5 Editing, Resubmitting & Quota Deduplication
Students are permitted up to **2 edits** after the initial submission (maximum of 3 submissions total per homework):
1. To replace an earlier submission, select the corresponding homework button (e.g., **HW 1**) prior to the deadline.
2. Upload the updated `.pdf` deliverable.
3. The bot automatically:
   - Increments the `edit_count` field in the database.
   - Deletes the superseded older message from the TA archive topic to prevent grader confusion.
   - Archives the newly uploaded document in the homework topic thread.
   - Updates the recorded timestamp and file name.
4. Once **2 edits** (3 submissions total) are reached, the submission slot is permanently locked:
   ```text
   ⚠️ Edit Limit Reached
   You have utilized all 2 permitted revisions for HW 1. Additional uploads for this assignment are disabled.
   ```

### 2.6 Checking Submission & Grading Status
Students can check their submission records at any time:
1. Tap the **📊 Status** button on the persistent keyboard, or send:
   ```text
   /status
   ```
2. The bot displays a status summary:
   ```text
   📋 Academic Submission Status: 4002019015
   • HW 1: Submitted (0 edits) | Score: 100/100
   • HW 2: Submitted (1 edit)  | Score: Pending
   • HW 3: Not submitted
   • HW 4: Not submitted
   • HW 5: Not submitted
   ```

### 2.7 Cancelling Operations
If you accidentally select a homework slot or want to return to the root menu:
- Tap the **❌ Cancel** button or type `/cancel`.
- Any active upload prompt is dismissed without modifying existing records.

### 2.8 Security Lockouts & Resolution Procedure
- **Trigger:** Entering an incorrect password **5 consecutive times**.
- **Result:** The account is blacklisted (`is_blacklisted = TRUE` in `user_state`). Subsequent messages return:
  ```text
  🚫 ACCESS DENIED
  Your account has been locked due to exceeding failed authentication attempts. Please contact your Course Teaching Assistant.
  ```
- **Resolution:** Contact your course TA via your official university communication channel and provide your **Student ID** and **Telegram Chat ID** (obtainable via `/id`). The TA will unblock your account via the TMA Control Hub or the `/unblock` command.

---

## 3. Part 2: Teaching Assistant & Instructor Admin Guide

### 3.1 Accessing the Telegram Mini App (TMA) Control Hub
Course Teaching Assistants and Instructors have access to the embedded **TA Control Hub**:
1. Open the course bot and send:
   ```text
   /admin
   ```
2. The bot verifies that your Telegram `chat_id` is present in `ADMIN_CHAT_IDS`.
3. Tap the **Open TA Control Hub** button to launch the full-screen Mini App.
4. **Zero-Trust Security:** The backend validates the cryptographic Telegram WebApp `initData` using HMAC-SHA256 with a 24-hour expiration window. Unauthorized access attempts are rejected with HTTP 401/403.

---

### 3.2 Tab 1: Live Roster & Submission Matrix
The **Matrix** tab provides real-time visibility across the entire enrolled student cohort:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 👥 Enrolled: 42   │ 👤 Registered: 38   │ 🔒 Locked: 1   │ 📥 Subs: 114 │
├────────────────────────────────────────────────────────────────────────┤
│ Student ID  Name            HW 1    HW 2    HW 3    HW 4    HW 5       │
│ 4001001     Alice Smith     ✓ (100) ✓ (95)  ✓ (0)   —       —          │
│ 4001002     Bob Jones       ✓ (85)  ✓ (90)  —       —       —          │
│ 4001003     Charlie Brown   🔒 LCK  —       —       —       —          │
└────────────────────────────────────────────────────────────────────────┘
```

1. **KPI Metrics Header:**
   - **Enrolled:** Total students recognized in the course roster.
   - **Registered:** Students who have linked their Telegram account.
   - **Locked:** Accounts currently blocked due to password failures.
   - **Submissions:** Total deliverables received across all assignments.
2. **Submission Indicators:**
   - `✓ (Score)`: Deliverable submitted and synced with Grade Flow scores.
   - `✓ (Pending)`: Deliverable submitted, waiting for grading.
   - `—`: Missing deliverable.
   - `🔒`: Student account locked out.
3. **Card Mode & Desktop Grid:** Automatically adapts between multi-column table layout on wide viewports and responsive touch-optimized cards on mobile devices.
4. **Deep Inspection:** Tapping any student row reveals their submission history, timestamps, edit counts, and direct archive references.

---

### 3.3 Tab 2: Broadcast Communication Console
The **Broadcast** tab enables instructors to dispatch targeted messages directly through the Telegram bot:

1. **Audience Targeting:**
   - **All Registered Students:** Broadcasts announcement to every enrolled student with an active Telegram binding.
   - **Missing HW (1–5):** Automatically targets students who have not yet submitted a specific homework deliverable.
   - **Single Student:** Sends a targeted direct message to an individual student selected from the roster dropdown.
2. **Rate-Limited Dispatching:**
   - Dispatches messages at safe intervals (under 25 ms per recipient) to avoid Telegram API flood controls (`429 Too Many Requests`).
   - Automatically detects and logs blocked users (`bot was blocked by the user`).
3. **Delivery Telemetry:** Displays total recipients, successfully dispatched count, and failure breakdown.

---

### 3.4 Tab 3: Unified Student Access & Account Control
The **Students** tab provides administrative control over student authentication states and session bindings:

```
┌───────────────────────────────────────────────────────────────────────┐
│ Student Access & Account Control                                      │
│ Search by Student ID, Name, or Telegram Chat ID...          [ Clear ] │
└───────────────────────────────────────────────────────────────────────┘
```

#### Smart Input & Chat ID Detection
The omni-search input dynamically interprets user intent:
1. **Search by Student ID or Name:** Typing `4002` or `Smith` instantly filters the roster and renders the matching student's full profile card.
2. **Real-Time Numeric Chat ID Detection:** Entering a 6–14 digit number (e.g., `6103177673`) immediately displays the **Telegram Chat ID Action Panel**:
   - If linked to an enrolled student: Displays their name, student ID, and registration timestamp.
   - If unregistered (e.g., failed registration during onboarding): Labels the account as **Unregistered Account** and offers immediate 1-click unblock/reset actions.

#### Critical Operational Distinction: Unblock vs Reset
Teaching Assistants must understand the precise difference between the two actions:

| Operation | Action Button | Endpoint | Impact on Database | When to Use |
| :--- | :--- | :--- | :--- | :--- |
| **Unblock Lockout** | `[Unblock Lockout]` / `[Unblock Account]` | `POST /api/admin/unblock` | Resets `user_state.attempts = 0`, sets `user_state.is_blacklisted = FALSE`. **Preserves student registration in `students` table.** | Student entered the wrong password 5 times. Account was locked, but their identity is confirmed. Unblock permits them to try password entry again immediately without re-registering. |
| **Reset Session** | `[Reset Session]` | `POST /api/admin/reset` | Deletes the row from `students` table. Resets `user_state.attempts = 0`, sets `is_blacklisted = FALSE`. **Historical submissions and grades are preserved.** | Student registered with the wrong Student ID, switched phones, or lost their Telegram account. Unbinds the account completely so `/start` triggers fresh registration. |

---

### 3.5 Telegram Chat In-Line Admin Commands
Teaching Assistants can execute administrative actions directly from their private Telegram chat with the bot without opening the Mini App:

- **`/unblock <student_id | chat_id>`**
  - Unblocks a student locked out by password failure.
  - Automatically recognizes whether an argument is a numeric Telegram Chat ID (8–12 digits) or a Student ID.
  - Example: `/unblock 4002019015` or `/unblock 6103177673`
- **`/reset <student_id | chat_id>`**
  - Unbinds a student's Telegram session and resets their lockout state so they can re-register from scratch.
  - Example: `/reset 4002019015`
- **`/missing <hw_num>`**
  - Generates an immediate list of students who have not yet submitted deliverable `hw_num` (1 to 5).
  - Automatically breaks long lists into cleanly chunked messages under Telegram's 4096-character limit.
  - Example: `/missing 1`
- **`/status`**
  - Administrative overview of submission counts and registration percentages across all 5 homework assignments.
  - Add a student ID to inspect individual progress: `/status 4002019015`.
- **`/id`**
  - Diagnostic command: replies with the sender's numeric Telegram `chat_id`, chat type, and thread topic ID. Essential when setting up supergroup archive topics.

---

## 4. Part 3: Grade Flow Web Platform & Supabase Sync

### 4.1 Gradebook Navigation & 2D Matrix Grid
The Grade Flow Web Application (`grade-flow-app`) provides the primary evaluation platform for instructors:
1. **Spreadsheet-Grade Matrix:** Features virtualized rendering supporting hundreds of enrolled students and assignments with zero input lag.
2. **Keyboard Ergonomics:** Supports arrow key navigation (`Up`, `Down`, `Left`, `Right`), `Tab`, `Shift+Tab`, and `Enter` for rapid grading.
3. **Real-Time Autosave:** Cell score edits trigger debounced mutations directly to the Supabase database.

---

### 4.2 Proportional Normalized Grading Engine
Grade Flow utilizes a normalized proportional grading calculation engine:

$$\text{Assignment Normalized Score} = \frac{\text{Earned Points}}{\text{Max Points}} \times \text{Weight}$$

$$\text{Final Course Grade} = \sum_{i=1}^{n} \text{Normalized Score}_i + \text{Bonus Points}$$

1. **Configurable Syllabus Weights:** Homework slots, midterm exams, quizzes, and final projects can each be assigned distinct proportional weights.
2. **Uncapped Bonus Overflow:** Bonus points overflow naturally into the semester total without being clamped by individual assignment maximums.
3. **Scale Target Mapping:** Seamlessly converts cumulative raw points into university letter scales or 0–20 standard grading ranges.

---

### 4.3 Database Architecture & Supabase Synchronization
The entire platform relies on a single managed PostgreSQL schema hosted on Supabase:

```
┌────────────────────────────────────────────────────────┐
│                        courses                         │
│ (id, name, term, grading_scale, created_at)            │
└──────────────────────────┬─────────────────────────────┘
                           │ 1:N
┌──────────────────────────▼─────────────────────────────┐
│                      assignments                       │
│ (id, course_id, title, max_score, weight, due_date)    │
└──────────────────────────┬─────────────────────────────┘
                           │ 1:N
┌──────────────────────────▼─────────────────────────────┐
│                         grades                         │
│ (id, assignment_id, student_id, earned_score, grader)  │
└──────────────────────────▲─────────────────────────────┘
                           │ N:1
┌──────────────────────────┴─────────────────────────────┐
│                        students                        │
│ (chat_id [PK], student_id [UQ], name, registered_at)   │
└──────────────────────────┬─────────────────────────────┘
                           │ 1:1
┌──────────────────────────▼─────────────────────────────┐
│                       user_state                       │
│ (chat_id [PK], attempts, is_blacklisted, pending_id)   │
└────────────────────────────────────────────────────────┘
```

1. **`v_student_grades_roster` View:** A PostgreSQL view aggregating student roster metadata, submitted homework deliverable links, and Grade Flow earned scores into a unified schema.
   - Built with `WITH (security_invoker = true)` to enforce strict Row-Level Security (RLS) rules.
2. **Connection Pooling:** 
   - Serverless functions (Vercel) connect via the **Supabase Transaction Pooler** on port `6543` (`?sslmode=require`) to prevent connection pool exhaustion.
   - Local migrations connect directly on port `5432`.

---

### 4.4 Roster Ingestion & Export Protocol
1. **Roster CSV Schema:** Rosters are loaded via `ROSTER_CSV` (or local file) with three comma-separated columns:
   ```csv
   student_id,name,password
   4002019001,Alice Smith,K9xP2wL4mQ
   4002019002,Bob Jones,T7vR5nB2yZ
   ```
2. **Gradebook Export:** The web app exports complete course gradebooks as CSV files formatted for university portal ingestion.

---

## 5. Part 4: Production Deployment & 1-Step Rollback Reference

### 5.1 Environment Configuration & Secrets Management
Both applications require environment variables configured in production (`.env` or Vercel Project Settings):

```bash
# Telegram Bot Core
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_test_token
ADMIN_CHAT_IDS=987654321,112233445
ARCHIVE_GROUP_ID=-1001234567890
WEBHOOK_HOST=https://your-bot-deployment.vercel.app

# Forum Topics (Thread IDs from supergroup)
TOPIC_HW1=2
TOPIC_HW2=4
TOPIC_HW3=6
TOPIC_HW4=8
TOPIC_HW5=10

# Security & Roster
REGISTRATION_PASSWORD=DefaultCoursePassword2026
ROSTER_CSV="student_id,name,password\n401001,Alice Smith,pwd1\n401002,Bob Jones,pwd2"

# Database Persistence (Supabase)
POSTGRES_URL=postgres://postgres.xxx:password@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require
```

> [!CAUTION] Security Mandate
> Never commit `.env`, `homework.db`, `students.csv`, or API credentials into Git. All secrets must be injected through hosting environment settings.

---

### 5.2 Vercel Serverless Function & Webhook Setup
The bot deploys as a serverless ASGI application via Vercel:
1. `api/index.py` handles Telegram webhook payloads (`POST /api/index` or `POST /`) and routes TMA requests (`/app`, `/api/admin/*`).
2. Telegram webhooks are registered automatically during application initialization, or manually via:
   ```bash
   curl -F "url=https://your-domain.vercel.app/api/index" https://api.telegram.org/bot<BOT_TOKEN>/setWebhook
   ```

---

### 5.3 Verification & Automated Test Suites
Run the automated test suite across all subsystems prior to production deployments:

```bash
cd "Bot & Grade Flow/bot"
python3 -m pytest tests -v
```

The test suite validates:
- Cryptographic HMAC-SHA256 signature verification and 24-hour replay guards.
- Password authentication, attempt throttling, and pending ID preservation.
- Administrative endpoints (`/api/admin/unblock`, `/api/admin/reset`, `/api/admin/broadcast`).
- Direct chat ID unblocking and roster matrix surfacing.
- Telegram bot command handlers (`/unblock`, `/reset`, `/missing`, `/status`).

---

### 5.4 Authoritative Disaster Recovery & Rollback Matrix
If a production issue occurs, execute a 1-step rollback to restore the repository to any verified historical release:

| Target Release | Version Tag | Key Architecture | 1-Step Rollback Command |
| :--- | :--- | :--- | :--- |
| **Current Production** | `v2.4.0` | Comprehensive tutorial & unified student access | `git reset --hard v2.4.0 && git push origin main --force` |
| **Unified Student Module** | `v2.3.0` | Unified Student Access Control & Chat ID detection | `git reset --hard v2.3.0 && git push origin main --force` |
| **Pre-Merge Checkpoint** | `v2.2-pre-module-merge` | Frozen two-card student management state | `git reset --hard v2.2-pre-module-merge && git push origin main --force` |
| **Lockout Engine Hotfix** | `v2.1.0-unblock-hotfix` | Parameterized SQL and `/unblock` commands | `git reset --hard v2.1.0-unblock-hotfix && git push origin main --force` |
| **Control Dashboard** | `v2.0.0-control-dashboard` | Terminology alignment & control dashboard standard | `git reset --hard v2.0.0-control-dashboard && git push origin main --force` |
| **Security Hardened** | `v1.1.0-security-hardened` | View security invoker & anon revocation | `git reset --hard v1.1.0-security-hardened && git push origin main --force` |
| **Baseline Turnkey** | `v1.0.0-baseline` | Initial unified monorepo release | `git reset --hard v1.0.0-baseline && git push origin main --force` |

---
*Document maintained by the Software Engineering Squad. Authoritative standard for all course operations.*
