import os
import uuid
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from main import (
    LISTING_STATUSES,
    LISTING_STATUS_LABELS,
    LOGO_PATH,
    UPLOADS_DIR,
    approve_message_request,
    authenticate_user,
    create_user,
    decline_message_request,
    fairness_score,
    format_timestamp,
    get_all_users_except,
    get_conversation_messages,
    get_message_requests,
    get_other_items_by_category,
    get_user_items,
    get_username_by_id,
    get_visible_conversations,
    init_db,
    mark_conversation_read,
    send_message,
    update_item_status,
)


app = Flask(__name__)
app.secret_key = os.getenv("WEB_SECRET_KEY", "cyber-xchange-web-dev")
init_db()


def current_user_id() -> int | None:
    user_id = session.get("user_id")
    return int(user_id) if user_id is not None else None


def current_username() -> str:
    return session.get("username", "")


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if current_user_id() is None:
            return redirect(url_for("auth"))
        return view_func(*args, **kwargs)

    return wrapped_view


def save_uploaded_web_photo(file_storage) -> str:
    """Save an uploaded browser file into the shared uploads folder."""
    if file_storage is None or not file_storage.filename:
        return ""

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    _, extension = os.path.splitext(file_storage.filename)
    safe_extension = extension or ".png"
    saved_name = f"{uuid.uuid4().hex}{safe_extension}"
    saved_path = os.path.join(UPLOADS_DIR, saved_name)
    file_storage.save(saved_path)
    return saved_path


def photo_url(photo_path: str) -> str | None:
    """Return a browser URL for a saved upload when possible."""
    if not photo_path:
        return None

    normalized = photo_path.replace("\\", "/")
    uploads_prefix = f"{UPLOADS_DIR}/"
    if normalized.startswith(uploads_prefix):
        return url_for("uploaded_file", filename=normalized[len(uploads_prefix):])

    if os.path.exists(photo_path):
        filename = os.path.basename(photo_path)
        candidate = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(candidate):
            return url_for("uploaded_file", filename=filename)

    return None


def web_price_links(term: str) -> list[tuple[str, str]]:
    from urllib.parse import quote_plus

    query = quote_plus(f"{term} new price")
    return [
        ("Google Shopping", f"https://www.google.com/search?tbm=shop&q={query}"),
        ("Google Search", f"https://www.google.com/search?q={query}"),
        ("eBay Search", f"https://www.ebay.com/sch/i.html?_nkw={query}"),
    ]


def decorate_listing_rows(items: list[tuple]) -> list[dict]:
    listings = []
    for item in items:
        if len(item) == 10:
            item_id, title, category, condition, description, estimated_value, desired_trade_value, photo_path, created_at, status = item
            owner_username = current_username()
            owner_id = current_user_id()
        else:
            item_id, owner_id, owner_username, title, category, condition, description, estimated_value, desired_trade_value, photo_path, created_at, status = item

        balance_label, balance_score = fairness_score(estimated_value, desired_trade_value)
        listings.append(
            {
                "id": item_id,
                "owner_id": owner_id,
                "owner_username": owner_username,
                "title": title,
                "category": category,
                "condition": condition,
                "description": description or "No description provided.",
                "estimated_value": estimated_value,
                "desired_trade_value": desired_trade_value,
                "photo_path": photo_path,
                "photo_url": photo_url(photo_path),
                "created_at": format_timestamp(created_at),
                "status": status,
                "status_label": LISTING_STATUS_LABELS.get(status, "Active"),
                "balance_label": balance_label,
                "balance_score": balance_score,
            }
        )
    return listings


def other_participant_id(conversation_meta: tuple, viewer_id: int) -> int:
    user_one_id = conversation_meta[3]
    user_two_id = conversation_meta[4]
    return user_two_id if user_one_id == viewer_id else user_one_id


@app.context_processor
def inject_shell_context():
    return {
        "shell_username": current_username(),
        "photo_url": photo_url,
    }


@app.route("/assets/logo")
def logo_file():
    if os.path.exists(LOGO_PATH):
        return send_from_directory(os.path.abspath("."), os.path.basename(LOGO_PATH))
    return ("", 404)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(os.path.abspath(UPLOADS_DIR), filename)


@app.route("/")
def index():
    if current_user_id() is not None:
        return redirect(url_for("hub"))
    return redirect(url_for("auth"))


@app.route("/auth", methods=["GET", "POST"])
def auth():
    if request.method == "POST":
        action = request.form.get("action", "login")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Enter a username and password.", "warning")
            return redirect(url_for("auth"))

        if action == "register":
            ok, message = create_user(username, password)
            flash(message, "success" if ok else "error")
            return redirect(url_for("auth"))

        ok, user_id = authenticate_user(username, password)
        if not ok or user_id is None:
            flash("Invalid username or password.", "error")
            return redirect(url_for("auth"))

        session["user_id"] = user_id
        session["username"] = username
        session["agreement_acknowledged"] = False
        return redirect(url_for("hub"))

    return render_template("auth.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth"))


@app.route("/acknowledge-agreement", methods=["POST"])
@login_required
def acknowledge_agreement():
    session["agreement_acknowledged"] = True
    return redirect(url_for("hub"))


@app.route("/hub")
@login_required
def hub():
    return render_template("hub.html", agreement_acknowledged=session.get("agreement_acknowledged", False))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "Other").strip()
        condition = request.form.get("condition", "Good").strip()
        description = request.form.get("description", "").strip()
        estimated_raw = request.form.get("estimated_value", "").strip()
        desired_raw = request.form.get("desired_trade_value", "").strip()

        if not title or not estimated_raw or not desired_raw:
            flash("Title, estimated value, and desired trade value are required.", "warning")
            return redirect(url_for("upload"))

        try:
            estimated_value = float(estimated_raw)
            desired_value = float(desired_raw)
        except ValueError:
            flash("Estimated value and desired trade value must be numbers.", "error")
            return redirect(url_for("upload"))

        saved_photo_path = ""
        try:
            saved_photo_path = save_uploaded_web_photo(request.files.get("photo"))
            from main import save_item

            save_item(
                current_user_id(),
                title,
                category,
                condition,
                description,
                estimated_value,
                desired_value,
                saved_photo_path,
            )
        except OSError as exc:
            flash(f"Could not save the image. {exc}", "error")
            return redirect(url_for("upload"))

        balance_label, balance_score = fairness_score(estimated_value, desired_value)
        flash(f"Listing saved. Trade balance reference: {balance_label} ({balance_score}%).", "success")
        return redirect(url_for("hub"))

    return render_template("upload.html")


@app.route("/browse")
@login_required
def browse():
    category = request.args.get("category", "All").strip() or "All"
    search_term = request.args.get("q", "").strip()
    market_term = request.args.get("market_q", "").strip()

    items = get_other_items_by_category(current_user_id(), category)
    if search_term:
        lowered = search_term.lower()
        items = [
            item
            for item in items
            if lowered in item[2].lower()
            or lowered in item[3].lower()
            or lowered in item[4].lower()
            or lowered in item[5].lower()
            or lowered in (item[6] or "").lower()
        ]

    market_links = web_price_links(market_term) if market_term else []
    return render_template(
        "browse.html",
        selected_category=category,
        search_term=search_term,
        market_term=market_term,
        market_links=market_links,
        listings=decorate_listing_rows(items),
    )


@app.route("/inbox", methods=["GET", "POST"])
@login_required
def inbox():
    user_id = current_user_id()
    assert user_id is not None

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "compose":
            recipient_username = request.form.get("recipient", "").strip()
            subject = request.form.get("subject", "").strip()
            body = request.form.get("body", "").strip()

            if not recipient_username or not subject or not body:
                flash("Recipient, subject, and message are required.", "warning")
                return redirect(url_for("inbox"))

            users = dict((username, member_id) for member_id, username in get_all_users_except(user_id))
            receiver_id = users.get(recipient_username)
            if receiver_id is None:
                flash("Selected user not found.", "error")
                return redirect(url_for("inbox"))

            conversation_id, status = send_message(user_id, receiver_id, subject, body)
            flash(
                "Message request sent." if status == "pending" else "Message sent.",
                "success",
            )
            return redirect(url_for("inbox", conv=conversation_id))

        if action == "reply":
            conversation_id = int(request.form.get("conversation_id", "0"))
            body = request.form.get("body", "").strip()
            if not conversation_id or not body:
                flash("Reply text is required.", "warning")
                return redirect(url_for("inbox", conv=conversation_id))

            meta, _messages = get_conversation_messages(user_id, conversation_id)
            if meta is None:
                flash("Conversation not found.", "error")
                return redirect(url_for("inbox"))

            receiver_id = other_participant_id(meta, user_id)
            send_message(user_id, receiver_id, "Trade Reply", body, conversation_id=conversation_id)
            return redirect(url_for("inbox", conv=conversation_id))

        if action == "approve":
            conversation_id = int(request.form.get("conversation_id", "0"))
            if approve_message_request(user_id, conversation_id):
                flash("Message request approved.", "success")
            return redirect(url_for("inbox", conv=conversation_id))

        if action == "decline":
            conversation_id = int(request.form.get("conversation_id", "0"))
            if decline_message_request(user_id, conversation_id):
                flash("Message request declined.", "success")
            return redirect(url_for("inbox"))

    requests = get_message_requests(user_id)
    conversations = get_visible_conversations(user_id)

    selected_conversation_id = request.args.get("conv", type=int)
    if selected_conversation_id is None and conversations:
        selected_conversation_id = conversations[0][0]

    selected_meta = None
    selected_messages: list[tuple] = []
    if selected_conversation_id is not None:
        selected_meta, selected_messages = get_conversation_messages(user_id, selected_conversation_id)
        if selected_meta is not None:
            mark_conversation_read(user_id, selected_conversation_id)

    users = get_all_users_except(user_id)
    return render_template(
        "inbox.html",
        requests=requests,
        conversations=conversations,
        selected_conversation_id=selected_conversation_id,
        selected_meta=selected_meta,
        selected_messages=selected_messages,
        users=users,
        current_user_id=user_id,
        format_timestamp=format_timestamp,
    )


@app.route("/profile")
@login_required
def profile():
    listings = decorate_listing_rows(get_user_items(current_user_id()))
    return render_template(
        "profile.html",
        listings=listings,
        listing_statuses=LISTING_STATUSES,
        listing_status_labels=LISTING_STATUS_LABELS,
    )


@app.route("/profile/status", methods=["POST"])
@login_required
def profile_status():
    item_id = request.form.get("item_id", type=int)
    next_status = request.form.get("status", "").strip().lower()
    if not item_id or next_status not in LISTING_STATUSES:
        flash("Choose a valid listing status.", "warning")
        return redirect(url_for("profile"))

    if update_item_status(current_user_id(), item_id, next_status):
        flash(f"Listing moved to {LISTING_STATUS_LABELS[next_status]}.", "success")
    else:
        flash("Could not update that listing.", "error")
    return redirect(url_for("profile"))


if __name__ == "__main__":
    app.run(debug=True)
