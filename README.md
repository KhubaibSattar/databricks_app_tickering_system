# Databricks Ticketing System App

A Databricks App for managing support tickets that:
- Connects to **Lakebase** (Databricks-managed Postgres) using a single `LAKEBASE_URL` secret (a native Postgres role with a static password)
- Provides a web UI for creating and viewing tickets
- Exposes a Flask API for ticket management
- Tracks ticket status, severity, escalation levels, and creation metadata

## Files

- `app.py` - Flask app: `/healthz`, `/tickets` (GET/POST/PUT/DELETE), `/tickets/<id>/messages` (GET/POST)
- `lakebase.py` - Lakebase connection helper (single `LAKEBASE_URL`, psycopg2 + SQLAlchemy)
- `templates/index.html` - Web UI for creating and viewing tickets
- `setup_secrets.py` - One-time script to create the secret scopes and store the Lakebase URL
- `app.yaml` - Databricks App deployment config (command + env vars)
- `.env.example` - Local dev env var template (copy to `.env`, do not commit real values)

## Step-by-step setup

### 1. Create a Lakebase instance and a native-password role

1. In your Databricks workspace, go to **Catalog** (left sidebar) and select the **Lakebase** tab (or search "Lakebase" in the workspace search bar).
2. Click **Create Lakebase instance** (sometimes labeled **Create database instance**).
   - Give it a name (e.g. `massive-sync-db`).
   - Choose the capacity/compute size and region appropriate for your workload (defaults are fine to start).
   - Click **Create** and wait for the instance to reach the **Available**/**Running** state.
3. Open the newly created instance, then go to the **Roles & Databases** tab (sometimes called **Permissions** or **Roles**).
4. **Enable native (password) authentication** for the instance if it isn't already on:
   - Look for an authentication setting such as **Native passwords** or **Password authentication** and toggle/enable it. By default some Lakebase instances only support OAuth/token-based auth — you need password auth enabled so the role below gets a static password instead of a short-lived token.
5. **Create a new role**:
   - Click **Add role** / **Create role**.
   - Choose **Password** as the authentication method (not OAuth).
   - Name the role (e.g. `massive_app`) and let Databricks generate (or set) a password.
6. **Copy the connection URL** shown for the role. It will look like:

   ```
   postgresql://<role>:<password>@<host>.database.cloud.databricks.com:5432/databricks_postgres?sslmode=require
   ```

   Keep this URL — you'll paste it into `setup_secrets.py`'s prompt in the next step.

### 2. Store your secrets

Run once from a **Databricks notebook** in your workspace (no CLI needed):

1. Create a new notebook (or open the Git folder you'll create in step 4, once it's cloned) and attach it to any running cluster.
2. In a cell, run:

   ```python
   %sh python setup_secrets.py
   ```

   or open a terminal from the notebook (**Run** > **Open terminal**, if enabled on your cluster) and run `python setup_secrets.py` there.

This prompts (via `getpass`, so nothing is echoed or written to disk/shell history) for:
- Your **Lakebase connection URL** (from step 1) → stored as secret `database/lakebase-url`

### 3. Configure environment variables (local dev)

Copy `.env.example` to `.env` and paste your Lakebase URL as `LAKEBASE_URL` for local runs:

```bash
cp .env.example .env
```

For deployment, `app.yaml` already pulls `LAKEBASE_URL` from the `database/lakebase-url` secret automatically — no manual editing needed there.

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run locally

```bash
python app.py
```

### 6. Create a Git folder in Databricks and deploy the app (no CLI required)

All of this is done through the Databricks workspace UI:

1. **Create a Git folder**:
   - In the Databricks workspace sidebar, click **Workspace** > **Create** > **Git folder** (in older UIs this is called **Repos** > **Add Repo**).
   - Paste the Git URL of this project's repository (e.g. your GitHub/GitLab remote for this codebase).
   - Choose a folder name and click **Create Git folder**. Databricks will clone the repo directly into your workspace — this becomes the source for your app.

2. **Create the Databricks App**:
   - In the sidebar, go to **Compute** > **Apps** (or search "Apps" in the workspace search bar).
   - Click **Create app**, then choose **Custom** (or "From scratch").
   - Give the app a name (e.g. `massive-lakebase-sync`).

3. **Point the app at your Git folder**:
   - When prompted for the source code location, select **Workspace files** / **Git folder** and browse to the Git folder you created in step 1 (the folder containing `app.py` and `app.yaml`).
   - Databricks will read `app.yaml` from that folder automatically to configure the `command` and `env` (including the `LAKEBASE_URL`, `MASSIVE_API_BASE_URL`, and secret scope/key references).

4. **Deploy**:
   - Click **Deploy** (or **Create and deploy**) in the Apps UI. Databricks will build and start the app using the Git folder's current contents — no `databricks` CLI commands are needed.
   - Whenever you update the code, pull the latest changes into the Git folder (**Git folder** > **Pull**, via the UI) and click **Deploy** again in the Apps UI to redeploy.

5. Once deployed, open the app's URL from the Apps UI to access the ticketing interface. You can also hit `GET /healthz` to confirm it's running.

## Endpoints

- `GET /` - Main UI for creating and viewing tickets
- `GET /healthz` - Health check
- `GET /tickets?limit=100` - List all tickets from Lakebase
- `POST /tickets` - Create a new ticket with JSON body:
  ```json
  {
    "title": "Issue description",
    "description": "Detailed description of the issue",
    "status": "Open",
    "severity": "Medium",
    "escalation": 0
  }
  ```
- `PUT /tickets/<ticket_id>` - Update an existing ticket with JSON body (all fields optional):
  ```json
  {
    "title": "Updated issue description",
    "description": "Updated detailed description",
    "status": "In Progress",
    "severity": "High",
    "escalation": 1
  }
  ```
- `DELETE /tickets/<ticket_id>` - Delete a ticket
- `GET /tickets/<ticket_id>/messages` - Get all messages for a ticket
- `POST /tickets/<ticket_id>/messages` - Add a new message to a ticket with JSON body:
  ```json
  {
    "message_text": "This is a message"
  }
  ```

## Ticket Schema

The tickets table has the following fields:
- `ticket_id` (SERIAL PRIMARY KEY) - Auto-incrementing ticket ID
- `title` (VARCHAR(255), NOT NULL) - Brief description of the issue
- `description` (TEXT) - Detailed description of the ticket
- `status` (VARCHAR(155)) - Current status (Open, In Progress, Resolved, Closed)
- `severity` (VARCHAR(155)) - Issue severity (Low, Medium, High, Critical)
- `escalation` (INT) - Escalation level (0-5)
- `created_by` (VARCHAR(255)) - Email of the user who created the ticket
- `created_at` (TIMESTAMPTZ) - Timestamp when the ticket was created

## Message Schema

The ticket_messages table has the following fields:
- `message_id` (SERIAL PRIMARY KEY) - Auto-incrementing message ID
- `ticket_id` (INT, FOREIGN KEY) - References the ticket this message belongs to
- `message_text` (VARCHAR(1000), NOT NULL) - The message content
- `author` (VARCHAR(255), NOT NULL) - Email of the user who wrote the message
- `created_at` (TIMESTAMPTZ) - Timestamp when the message was created

## Features

### ✅ Implemented
- ✅ Create new tickets with title, description, status, severity, and escalation level
- ✅ View all tickets in a sortable table with truncated descriptions (hover to see full text)
- ✅ Edit existing tickets via modal popup
- ✅ Delete tickets with confirmation dialog
- ✅ User tracking (automatically captures creator's email)
- ✅ Color-coded status and severity badges
- ✅ Responsive modal UI with smooth animations
- ✅ Description field with textarea input and hover tooltip for full text
- ✅ **Message threads** - Click any ticket row to view its conversation thread
- ✅ **Add messages** - Write messages on tickets with automatic author tracking
- ✅ **Real-time conversation** - Messages display in chronological order with timestamps

### 🚀 Future Enhancements
- Add filtering and search functionality
- Add user assignment and notifications
- Add comments/notes on tickets
- Add file attachments
- Add ticket history/audit log
- Enable Change Data Feed (CDF) to stream ticket changes to Unity Catalog Delta tables
