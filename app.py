"""
Databricks Ticketing System App:
- Serves a Flask API for ticket management
- Reads/writes tickets to Lakebase (Databricks-managed Postgres) via lakebase.py

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import logging
import os

from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request

import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticketing-app")

app = Flask(__name__)
_w = WorkspaceClient()

TICKETS_TABLE_NAME = os.environ.get("TICKETS_TABLE_NAME", "tickets")
MESSAGES_TABLE_NAME = os.environ.get("MESSAGES_TABLE_NAME", "ticket_messages")


def ensure_tickets_table():
    """Create the tickets table in Lakebase if it doesn't exist yet."""
    # Create sequence for ticket_id if it doesn't exist
    lakebase.run_write(
        f"CREATE SEQUENCE IF NOT EXISTS {TICKETS_TABLE_NAME}_ticket_id_seq"
    )
    
    # Create table if it doesn't exist
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {TICKETS_TABLE_NAME} (
            ticket_id INTEGER PRIMARY KEY DEFAULT nextval('{TICKETS_TABLE_NAME}_ticket_id_seq'),
            title VARCHAR(255) NOT NULL,
            description TEXT,
            status VARCHAR(155) NOT NULL DEFAULT 'Open',
            severity VARCHAR(155) NOT NULL DEFAULT 'Medium',
            escalation INT DEFAULT 0,
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    
    # Migration: Add description column if it doesn't exist
    # This handles existing tables that were created before the description field was added
    lakebase.run_write(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{TICKETS_TABLE_NAME}' AND column_name = 'description'
            ) THEN
                ALTER TABLE {TICKETS_TABLE_NAME} ADD COLUMN description TEXT;
            END IF;
        END $$;
        """
    )


def ensure_messages_table():
    """Create the messages table in Lakebase if it doesn't exist yet."""
    # Create sequence for message_id if it doesn't exist
    lakebase.run_write(
        f"CREATE SEQUENCE IF NOT EXISTS {MESSAGES_TABLE_NAME}_message_id_seq"
    )
    
    # Create table if it doesn't exist
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE_NAME} (
            message_id INTEGER PRIMARY KEY DEFAULT nextval('{MESSAGES_TABLE_NAME}_message_id_seq'),
            ticket_id INT NOT NULL,
            message_text VARCHAR(1000) NOT NULL,
            author VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (ticket_id) REFERENCES {TICKETS_TABLE_NAME}(ticket_id) ON DELETE CASCADE
        )
        """
    )


def fix_existing_sequences():
    """Fix existing tables that might not have proper sequence defaults."""
    try:
        # Fix tickets table if it exists without proper default
        lakebase.run_write(
            f"""
            DO $
            BEGIN
                -- Ensure sequence exists
                IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname = 'public' AND sequencename = '{TICKETS_TABLE_NAME}_ticket_id_seq') THEN
                    CREATE SEQUENCE {TICKETS_TABLE_NAME}_ticket_id_seq;
                END IF;
                
                -- Set the sequence to start from the max existing ID + 1
                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{TICKETS_TABLE_NAME}') THEN
                    PERFORM setval('{TICKETS_TABLE_NAME}_ticket_id_seq', COALESCE((SELECT MAX(ticket_id) FROM {TICKETS_TABLE_NAME}), 0) + 1, false);
                END IF;
                
                -- Ensure the default is set on the column
                ALTER TABLE {TICKETS_TABLE_NAME} ALTER COLUMN ticket_id SET DEFAULT nextval('{TICKETS_TABLE_NAME}_ticket_id_seq');
                
                -- Ensure sequence ownership
                ALTER SEQUENCE {TICKETS_TABLE_NAME}_ticket_id_seq OWNED BY {TICKETS_TABLE_NAME}.ticket_id;
            EXCEPTION
                WHEN OTHERS THEN
                    -- Ignore errors if table doesn't exist yet
                    NULL;
            END $;
            """
        )
        
        # Fix messages table if it exists without proper default
        lakebase.run_write(
            f"""
            DO $
            BEGIN
                -- Ensure sequence exists
                IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname = 'public' AND sequencename = '{MESSAGES_TABLE_NAME}_message_id_seq') THEN
                    CREATE SEQUENCE {MESSAGES_TABLE_NAME}_message_id_seq;
                END IF;
                
                -- Set the sequence to start from the max existing ID + 1
                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{MESSAGES_TABLE_NAME}') THEN
                    PERFORM setval('{MESSAGES_TABLE_NAME}_message_id_seq', COALESCE((SELECT MAX(message_id) FROM {MESSAGES_TABLE_NAME}), 0) + 1, false);
                END IF;
                
                -- Ensure the default is set on the column
                ALTER TABLE {MESSAGES_TABLE_NAME} ALTER COLUMN message_id SET DEFAULT nextval('{MESSAGES_TABLE_NAME}_message_id_seq');
                
                -- Ensure sequence ownership
                ALTER SEQUENCE {MESSAGES_TABLE_NAME}_message_id_seq OWNED BY {MESSAGES_TABLE_NAME}.message_id;
            EXCEPTION
                WHEN OTHERS THEN
                    -- Ignore errors if table doesn't exist yet
                    NULL;
            END $;
            """
        )
        logger.info("Successfully fixed existing sequences for both tables")
    except Exception as e:
        logger.warning(f"Error fixing existing sequences (this is OK if tables don't exist yet): {e}")


def _current_user_email() -> str:
    """
    Resolve the current user's email for ticket tracking.

    Databricks Apps inject the logged-in user's identity via the
    X-Forwarded-Email header on every request. Fall back to the Databricks
    SDK's current_user API for local development where that header isn't set.
    """
    header_email = request.headers.get("X-Forwarded-Email")
    if header_email:
        return header_email
    return _w.current_user.me().user_name


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page),
    so the frontend's resp.json() call never chokes on HTML."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Simple UI to submit and view tickets."""
    return render_template("index.html")


@app.route("/messages")
def messages():
    """Messages page for a specific ticket."""
    return render_template("messages.html")


@app.route("/current_user")
def current_user():
    """Return the current logged-in user's email."""
    return jsonify({"email": _current_user_email()})


@app.route("/debug/database")
def debug_database():
    """Debug endpoint to verify database connection and table state."""
    try:
        ensure_tickets_table()
        ensure_messages_table()
        
        # Check if tables exist
        tables_info = lakebase.run_query(
            f"""SELECT table_name FROM information_schema.tables 
               WHERE table_schema = 'public' 
               AND table_name IN ('{TICKETS_TABLE_NAME}', '{MESSAGES_TABLE_NAME}')"""
        )
        
        # Get row counts
        tickets_count = lakebase.run_query(f"SELECT COUNT(*) as count FROM {TICKETS_TABLE_NAME}")[0]['count']
        messages_count = lakebase.run_query(f"SELECT COUNT(*) as count FROM {MESSAGES_TABLE_NAME}")[0]['count']
        
        # Get sample tickets
        sample_tickets = lakebase.run_query(
            f"SELECT * FROM {TICKETS_TABLE_NAME} ORDER BY created_at DESC LIMIT 5"
        )
        
        return jsonify({
            "status": "ok",
            "tables_found": [t['table_name'] for t in tables_info],
            "tickets_count": tickets_count,
            "messages_count": messages_count,
            "sample_tickets": sample_tickets,
            "table_name": TICKETS_TABLE_NAME
        })
    except Exception as e:
        logger.exception("Debug endpoint failed")
        return jsonify({
            "status": "error",
            "error": str(e),
            "type": type(e).__name__
        }), 500


@app.route("/tickets", methods=["GET"])
def list_tickets():
    """Retrieve all tickets from the database."""
    ensure_tickets_table()
    ensure_messages_table()
    limit = int(request.args.get("limit", 100))
    rows = lakebase.run_query(
        f"SELECT ticket_id, title, description, status, severity, escalation, created_by, created_at "
        f"FROM {TICKETS_TABLE_NAME} ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    return jsonify(rows)


@app.route("/tickets", methods=["POST"])
def create_ticket():
    """
    Create a new ticket in the database.
    """
    ensure_tickets_table()
    ensure_messages_table()
    
    if request.is_json:
        title = request.json.get("title", "")
        description = request.json.get("description", "")
        status = request.json.get("status", "Open")
        severity = request.json.get("severity", "Medium")
        escalation = request.json.get("escalation", 0)
    else:
        title = request.form.get("title", "")
        description = request.form.get("description", "")
        status = request.form.get("status", "Open")
        severity = request.form.get("severity", "Medium")
        escalation = request.form.get("escalation", 0)
    
    title = title.strip() if isinstance(title, str) else ""
    description = description.strip() if isinstance(description, str) else ""
    
    if not title:
        return jsonify({"error": "Title is required"}), 400
    
    # Get the current user's email
    created_by = _current_user_email()
    
    # Convert escalation to int
    try:
        escalation = int(escalation)
    except (ValueError, TypeError):
        escalation = 0
    
    # Insert the ticket
    logger.info(f"Creating ticket: title='{title}', status='{status}', severity='{severity}'")
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {TICKETS_TABLE_NAME} (title, description, status, severity, escalation, created_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                RETURNING ticket_id, title, description, status, severity, escalation, created_by, created_at
                """,
                (title, description, status, severity, escalation, created_by),
            )
            result = cur.fetchone()
            conn.commit()
            logger.info(f"Ticket created successfully: ticket_id={result['ticket_id']}")
    
    return jsonify(result)


@app.route("/tickets/<int:ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
    """
    Update an existing ticket in the database.
    """
    ensure_tickets_table()
    
    if request.is_json:
        title = request.json.get("title")
        description = request.json.get("description")
        status = request.json.get("status")
        severity = request.json.get("severity")
        escalation = request.json.get("escalation")
    else:
        title = request.form.get("title")
        description = request.form.get("description")
        status = request.form.get("status")
        severity = request.form.get("severity")
        escalation = request.form.get("escalation")
    
    # Build update query dynamically based on provided fields
    updates = []
    params = []
    
    if title is not None:
        title = title.strip() if isinstance(title, str) else ""
        if not title:
            return jsonify({"error": "Title cannot be empty"}), 400
        updates.append("title = %s")
        params.append(title)
    
    if description is not None:
        description = description.strip() if isinstance(description, str) else ""
        updates.append("description = %s")
        params.append(description)
    
    if status is not None:
        updates.append("status = %s")
        params.append(status)
    
    if severity is not None:
        updates.append("severity = %s")
        params.append(severity)
    
    if escalation is not None:
        try:
            escalation = int(escalation)
            updates.append("escalation = %s")
            params.append(escalation)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid escalation value"}), 400
    
    if not updates:
        return jsonify({"error": "No fields to update"}), 400
    
    params.append(ticket_id)
    
    # Update the ticket
    logger.info(f"Updating ticket {ticket_id}: fields={', '.join([u.split(' = ')[0] for u in updates])}")
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {TICKETS_TABLE_NAME}
                SET {', '.join(updates)}
                WHERE ticket_id = %s
                RETURNING ticket_id, title, description, status, severity, escalation, created_by, created_at
                """,
                tuple(params),
            )
            result = cur.fetchone()
            if not result:
                logger.warning(f"Ticket {ticket_id} not found for update")
                conn.rollback()
                return jsonify({"error": "Ticket not found"}), 404
            conn.commit()
            logger.info(f"Ticket {ticket_id} updated successfully")
    
    return jsonify(result)


@app.route("/tickets/<int:ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id):
    """
    Delete a ticket from the database.
    First deletes all associated messages due to foreign key constraint.
    """
    ensure_tickets_table()
    ensure_messages_table()
    
    logger.info(f"Deleting ticket {ticket_id}")
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            # First, delete all messages associated with this ticket
            cur.execute(
                f"DELETE FROM {MESSAGES_TABLE_NAME} WHERE ticket_id = %s",
                (ticket_id,),
            )
            messages_deleted = cur.rowcount
            logger.info(f"Deleted {messages_deleted} messages for ticket {ticket_id}")
            
            # Then, delete the ticket itself
            cur.execute(
                f"DELETE FROM {TICKETS_TABLE_NAME} WHERE ticket_id = %s",
                (ticket_id,),
            )
            if cur.rowcount == 0:
                logger.warning(f"Ticket {ticket_id} not found for deletion")
                conn.rollback()
                return jsonify({"error": "Ticket not found"}), 404
            
            conn.commit()
            logger.info(f"Ticket {ticket_id} deleted successfully")
    
    return jsonify({
        "message": "Ticket deleted successfully", 
        "ticket_id": ticket_id,
        "messages_deleted": messages_deleted
    })


@app.route("/tickets/<int:ticket_id>/messages", methods=["GET"])
def get_ticket_messages(ticket_id):
    """
    Retrieve all messages for a specific ticket.
    """
    ensure_messages_table()
    rows = lakebase.run_query(
        f"""SELECT message_id, ticket_id, message_text, author, created_at 
           FROM {MESSAGES_TABLE_NAME} 
           WHERE ticket_id = %s 
           ORDER BY created_at ASC""",
        (ticket_id,),
    )
    return jsonify(rows)


@app.route("/tickets/<int:ticket_id>/messages", methods=["POST"])
def create_ticket_message(ticket_id):
    """
    Create a new message for a specific ticket.
    """
    ensure_messages_table()
    
    if request.is_json:
        message_text = request.json.get("message_text", "")
    else:
        message_text = request.form.get("message_text", "")
    
    message_text = message_text.strip() if isinstance(message_text, str) else ""
    
    if not message_text:
        return jsonify({"error": "Message text is required"}), 400
    
    # Get the current user's email
    author = _current_user_email()
    
    # Insert the message
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {MESSAGES_TABLE_NAME} (ticket_id, message_text, author, created_at)
                VALUES (%s, %s, %s, now())
                RETURNING message_id, ticket_id, message_text, author, created_at
                """,
                (ticket_id, message_text, author),
            )
            result = cur.fetchone()
            conn.commit()
    
    return jsonify(result)


if __name__ == '__main__':
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_RUN_PORT', 8000))
    app.run(debug=True, host=host, port=port)
    print(f"Flask app running on http://{host}:{port}")