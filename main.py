import os
import sqlite3
import math
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


# -------------------------------------------------
# App constants
# -------------------------------------------------
DB_NAME = "CyberXchange.db"
LOGO_PATH = "Xchange.png"   # Change this if your image file name is different
APP_TITLE = "Cyber Xchange"
WINDOW_SIZE = "1240x820"

# -------------------------------------------------
# Neon theme colors
# -------------------------------------------------
BG_MAIN = "#04070d"
BG_PANEL = "#0b1020"
BG_CARD = "#11182b"
BG_INPUT = "#09111f"
BG_ALT = "#0d1324"

NEON_BLUE = "#00e5ff"
NEON_GREEN = "#39ff14"
NEON_PINK = "#ff4df0"
NEON_PURPLE = "#a855f7"
TEXT_MAIN = "#d9ffff"
TEXT_SOFT = "#9adcf2"
TEXT_MUTED = "#7fa8c4"


# -------------------------------------------------
# Database setup
# -------------------------------------------------
def init_db() -> None:
    """Create required tables if they do not already exist."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Item listings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            condition TEXT NOT NULL,
            description TEXT,
            estimated_value REAL NOT NULL,
            desired_trade_value REAL NOT NULL,
            photo_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # Local inbox/messages table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            item_id INTEGER,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
        """
    )

    conn.commit()
    conn.close()


# -------------------------------------------------
# User/account helpers
# -------------------------------------------------
def create_user(username: str, password: str) -> tuple[bool, str]:
    """Create a new user account."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
            (username.strip(), password.strip(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return True, "Account created successfully."
    except sqlite3.IntegrityError:
        return False, "That username already exists."


def authenticate_user(username: str, password: str) -> tuple[bool, int | None]:
    """Check if a username/password pair exists."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM users WHERE username = ? AND password = ?",
        (username.strip(), password.strip()),
    )
    row = cur.fetchone()
    conn.close()

    if row:
        return True, row[0]
    return False, None


def get_username_by_id(user_id: int) -> str:
    """Return a username from a user id."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Unknown"


def get_all_users_except(user_id: int) -> list[tuple[int, str]]:
    """Return all users except the currently logged-in user."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username FROM users WHERE id != ? ORDER BY username COLLATE NOCASE",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------
# Item/listing helpers
# -------------------------------------------------
def save_item(
    user_id: int,
    title: str,
    category: str,
    condition: str,
    description: str,
    estimated_value: float,
    desired_trade_value: float,
    photo_path: str,
) -> None:
    """Save a new barter listing."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO items (
            user_id, title, category, condition, description,
            estimated_value, desired_trade_value, photo_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            title.strip(),
            category.strip(),
            condition.strip(),
            description.strip(),
            estimated_value,
            desired_trade_value,
            photo_path.strip(),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_user_items(user_id: int) -> list[tuple]:
    """Return all listings for a specific user."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, category, condition, description,
               estimated_value, desired_trade_value, photo_path, created_at
        FROM items
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_other_items(user_id: int) -> list[tuple]:
    """Return all listings from other users."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
               i.description, i.estimated_value, i.desired_trade_value,
               i.photo_path, i.created_at
        FROM items i
        JOIN users u ON i.user_id = u.id
        WHERE i.user_id != ?
        ORDER BY i.id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_other_items_by_category(user_id: int, category: str) -> list[tuple]:
    """Return filtered listings from other users by category."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    if category == "All":
        cur.execute(
            """
            SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
                   i.description, i.estimated_value, i.desired_trade_value,
                   i.photo_path, i.created_at
            FROM items i
            JOIN users u ON i.user_id = u.id
            WHERE i.user_id != ?
            ORDER BY i.id DESC
            """,
            (user_id,),
        )
    else:
        cur.execute(
            """
            SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
                   i.description, i.estimated_value, i.desired_trade_value,
                   i.photo_path, i.created_at
            FROM items i
            JOIN users u ON i.user_id = u.id
            WHERE i.user_id != ? AND i.category = ?
            ORDER BY i.id DESC
            """,
            (user_id, category),
        )

    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------
# Message helpers
# -------------------------------------------------
def send_message(sender_id: int, receiver_id: int, subject: str, body: str, item_id: int | None = None) -> None:
    """Save a local message in the app inbox."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO messages (sender_id, receiver_id, subject, body, item_id, created_at, is_read)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (
            sender_id,
            receiver_id,
            subject.strip(),
            body.strip(),
            item_id,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_inbox_messages(user_id: int) -> list[tuple]:
    """Return messages received by the current user."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.id, u.username, m.subject, m.body, m.created_at, m.is_read
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.receiver_id = ?
        ORDER BY m.id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------
# Trade scoring / matching
# -------------------------------------------------
def fairness_score(my_value: float, target_value: float) -> tuple[str, float]:
    """Compare two item values and return a trade balance label."""
    if my_value <= 0 or target_value <= 0:
        return "Unknown", 0.0

    ratio = min(my_value, target_value) / max(my_value, target_value)
    score = round(ratio * 100, 1)

    if score >= 90:
        label = "Excellent trade balance"
    elif score >= 75:
        label = "Good trade balance"
    elif score >= 60:
        label = "Possible trade gap"
    else:
        label = "Unbalanced trade"

    return label, score


# -------------------------------------------------
# Main UI app
# -------------------------------------------------
class SilkRouteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(1120, 760)
        self.configure(bg=BG_MAIN)

        # Current session state
        self.current_user_id: int | None = None
        self.current_username: str = ""
        self.selected_photo_path: str = ""
        self.logo_photo = None
        self.preview_photo = None
        self.background_canvas: tk.Canvas | None = None
        self.pulse_phase = 0.0
        self.pulse_job = None

        # ttk styling
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure_styles()

        # Main screen container
        self.main_container = tk.Frame(self, bg=BG_MAIN)
        self.main_container.pack(fill="both", expand=True)
        self.bind("<Configure>", self.on_window_resize)

        # Start on login screen
        self.show_login_screen()

    # -------------------------------------------------
    # Styling
    # -------------------------------------------------
    def configure_styles(self) -> None:
        """Set global ttk styles for the neon theme."""
        self.style.configure("TFrame", background=BG_MAIN)

        self.style.configure(
            "Header.TLabel",
            background=BG_MAIN,
            foreground=NEON_BLUE,
            font=("Consolas", 28, "bold"),
        )

        self.style.configure(
            "SubHeader.TLabel",
            background=BG_MAIN,
            foreground=TEXT_SOFT,
            font=("Consolas", 12),
        )

        self.style.configure(
            "Accent.TButton",
            background=BG_PANEL,
            foreground=NEON_BLUE,
            font=("Consolas", 11, "bold"),
            padding=10,
            borderwidth=1,
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", NEON_BLUE)],
            foreground=[("active", BG_MAIN)],
        )

        self.style.configure(
            "Secondary.TButton",
            background=BG_PANEL,
            foreground=NEON_GREEN,
            font=("Consolas", 10, "bold"),
            padding=8,
            borderwidth=1,
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", NEON_GREEN)],
            foreground=[("active", BG_MAIN)],
        )

        self.style.configure(
            "TEntry",
            fieldbackground=BG_INPUT,
            foreground=NEON_GREEN,
            insertcolor=NEON_BLUE,
            padding=8,
            font=("Consolas", 10),
        )

        self.style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            foreground=NEON_GREEN,
            padding=6,
            font=("Consolas", 10),
        )

    # -------------------------------------------------
    # Utility helpers
    # -------------------------------------------------
    def clear_screen(self) -> None:
        """Remove all widgets from the current screen."""
        if self.pulse_job is not None:
            self.after_cancel(self.pulse_job)
            self.pulse_job = None
        for widget in self.main_container.winfo_children():
            widget.destroy()
        self.background_canvas = None

    def on_window_resize(self, event) -> None:
        """Redraw the animated background when the window size changes."""
        if event.widget == self and self.background_canvas is not None:
            self.draw_neon_background()

    def create_neon_background(self) -> None:
        """Create a reusable animated cyberpunk backdrop for the active screen."""
        self.background_canvas = tk.Canvas(
            self.main_container,
            bg=BG_MAIN,
            highlightthickness=0,
            bd=0,
        )
        self.background_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.background_canvas.lower()
        self.draw_neon_background()
        self.animate_neon_background()

    def animate_neon_background(self) -> None:
        """Pulse the neon glow slowly to add motion without distracting from the UI."""
        if self.background_canvas is None:
            self.pulse_job = None
            return

        self.pulse_phase = (self.pulse_phase + 0.12) % (2 * math.pi)
        self.draw_neon_background()
        self.pulse_job = self.after(90, self.animate_neon_background)

    def draw_neon_background(self) -> None:
        """Paint the shared neon background behind the current page."""
        if self.background_canvas is None:
            return

        canvas = self.background_canvas
        width = max(self.main_container.winfo_width(), 1240)
        height = max(self.main_container.winfo_height(), 820)
        pulse = (math.sin(self.pulse_phase) + 1) / 2

        canvas.delete("all")
        canvas.configure(bg=BG_MAIN)

        canvas.create_rectangle(0, 0, width, height, fill=BG_MAIN, outline="")
        canvas.create_rectangle(0, 0, width, int(height * 0.28), fill="#06111d", outline="")
        canvas.create_rectangle(0, int(height * 0.68), width, height, fill="#07101a", outline="")

        for x in range(0, width, 60):
            canvas.create_line(x, 0, x, height, fill="#10213a", width=1)
        for y in range(0, height, 60):
            canvas.create_line(0, y, width, y, fill="#0e1b31", width=1)

        canvas.create_line(-80, height * 0.2, width * 0.45, -40, fill="#123050", width=3)
        canvas.create_line(width * 0.55, height + 40, width + 60, height * 0.55, fill="#1b2848", width=3)
        canvas.create_line(width * 0.15, height + 20, width * 0.55, height * 0.45, fill="#0d2540", width=2)

        glow_specs = [
            (int(width * 0.12), int(height * 0.16), 250, NEON_BLUE),
            (int(width * 0.82), int(height * 0.22), 220, NEON_PINK),
            (int(width * 0.28), int(height * 0.82), 210, NEON_GREEN),
            (int(width * 0.78), int(height * 0.78), 260, NEON_PURPLE),
        ]
        for center_x, center_y, radius, color in glow_specs:
            for index, (scale, outline_width) in enumerate(((1.5, 1), (1.16, 2), (0.78, 3))):
                breathing_scale = 1 + ((pulse - 0.5) * 0.12 * (index + 1))
                offset = int(radius * scale * breathing_scale)
                canvas.create_oval(
                    center_x - offset,
                    center_y - offset,
                    center_x + offset,
                    center_y + offset,
                    outline=color,
                    width=outline_width,
                )

        canvas.create_rectangle(26, 26, width - 26, 30, fill=NEON_BLUE, outline="")
        canvas.create_rectangle(26, height - 30, width - 26, height - 26, fill=NEON_PINK, outline="")

        node_shift = int(6 * pulse)
        for x, y, color in (
            (70 + node_shift, 70, NEON_BLUE),
            (width - 90 - node_shift, 84, NEON_PINK),
            (110, height - 90 - node_shift, NEON_GREEN),
            (width - 130, height - 110 + node_shift, NEON_PURPLE),
        ):
            canvas.create_oval(x, y, x + 18, y + 18, fill=color, outline="")

    def logout(self) -> None:
        """Clear session state and return to login."""
        self.current_user_id = None
        self.current_username = ""
        self.selected_photo_path = ""
        self.preview_photo = None
        self.show_login_screen()

    def build_topbar(self, title_text: str, back_command=None) -> tk.Frame:
        """Reusable neon top bar for app sections."""
        topbar = tk.Frame(self.main_container, bg=BG_MAIN)
        topbar.pack(fill="x", padx=20, pady=(16, 8))

        left_side = tk.Frame(topbar, bg=BG_MAIN)
        left_side.pack(side="left")

        if back_command is not None:
            ttk.Button(
                left_side,
                text="← Back",
                command=back_command,
                style="Secondary.TButton",
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            left_side,
            text=title_text,
            bg=BG_MAIN,
            fg=NEON_BLUE,
            font=("Consolas", 22, "bold"),
        ).pack(side="left")

        right_side = tk.Frame(topbar, bg=BG_MAIN)
        right_side.pack(side="right")

        if self.current_username:
            tk.Label(
                right_side,
                text=f"USER: {self.current_username}",
                bg=BG_MAIN,
                fg=NEON_GREEN,
                font=("Consolas", 11, "bold"),
            ).pack(side="left", padx=(0, 12))

        ttk.Button(
            right_side,
            text="Logout",
            command=self.logout,
            style="Secondary.TButton",
        ).pack(side="left")

        return topbar

    def load_logo_widget(self, parent: tk.Widget, size: tuple[int, int] = (220, 220)) -> tk.Label:
        """Load the app logo image if present, otherwise show text."""
        label = tk.Label(parent, bg=BG_MAIN)

        if Image and os.path.exists(LOGO_PATH):
            image = Image.open(LOGO_PATH)
            image = image.resize(size)
            self.logo_photo = ImageTk.PhotoImage(image)
            label.configure(image=self.logo_photo, bd=0, highlightthickness=0)
        else:
            label.configure(
                text="CYBER\nXCHANGE",
                fg=NEON_BLUE,
                bg=BG_MAIN,
                font=("Consolas", 24, "bold"),
                justify="center",
            )
        return label

    def make_card_button(
        self,
        parent: tk.Widget,
        title: str,
        subtitle: str,
        fg_color: str,
        command,
    ) -> tk.Frame:
        """Build a large clickable menu card."""
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            highlightbackground=fg_color,
            highlightthickness=1,
            cursor="hand2",
        )

        title_label = tk.Label(
            card,
            text=title,
            bg=BG_CARD,
            fg=fg_color,
            font=("Consolas", 18, "bold"),
        )
        title_label.pack(anchor="w", padx=18, pady=(18, 6))

        subtitle_label = tk.Label(
            card,
            text=subtitle,
            bg=BG_CARD,
            fg=TEXT_SOFT,
            font=("Consolas", 10),
            justify="left",
            wraplength=260,
        )
        subtitle_label.pack(anchor="w", padx=18, pady=(0, 18))

        def click_card(event=None):
            command()

        card.bind("<Button-1>", click_card)
        title_label.bind("<Button-1>", click_card)
        subtitle_label.bind("<Button-1>", click_card)

        return card

    # -------------------------------------------------
    # Login / Register screen
    # -------------------------------------------------
    def show_login_screen(self) -> None:
        self.clear_screen()
        self.create_neon_background()

        wrapper = tk.Frame(self.main_container, bg=BG_MAIN)
        wrapper.pack(fill="both", expand=True, padx=40, pady=40)

        left = tk.Frame(wrapper, bg=BG_MAIN)
        left.pack(side="left", fill="both", expand=True, padx=(0, 20))

        right = tk.Frame(
            wrapper,
            bg=BG_PANEL,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        right.pack(side="right", fill="y", padx=(20, 0))
        right.configure(width=430)
        right.pack_propagate(False)

        logo = self.load_logo_widget(left, size=(320, 320))
        logo.pack(anchor="center", pady=(20, 10))

        ttk.Label(left, text="TRADE SMARTER. TRADE FAIRER.", style="Header.TLabel").pack(anchor="center", pady=(0, 8))
        ttk.Label(
            left,
            text=(
                "Cyber Xchange is a neon-styled barter platform prototype. "
                "Users create accounts, browse trade listings, compare value, "
                "and connect through a local inbox."
            ),
            style="SubHeader.TLabel",
            wraplength=620,
            justify="center",
        ).pack(anchor="center", pady=(0, 20))

        features = tk.Frame(left, bg=BG_MAIN)
        features.pack(anchor="center", pady=20)

        bullets = [
            "• Secure local account creation and login",
            "• Upload item listings for trade only",
            "• Browse other users by category",
            "• Compare estimated value vs desired trade value",
            "• Message traders through the app inbox",
        ]
        for item in bullets:
            tk.Label(
                features,
                text=item,
                bg=BG_MAIN,
                fg=NEON_GREEN,
                font=("Consolas", 11),
                anchor="w",
                justify="left",
            ).pack(anchor="w", pady=3)

        form = tk.Frame(right, bg=BG_PANEL)
        form.pack(fill="both", expand=True, padx=30, pady=30)

        tk.Label(
            form,
            text="LOGIN / REGISTER",
            bg=BG_PANEL,
            fg=NEON_PINK,
            font=("Consolas", 20, "bold"),
        ).pack(anchor="w", pady=(10, 25))

        tk.Label(form, text="Username", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.username_entry = ttk.Entry(form, width=30)
        self.username_entry.pack(fill="x", pady=(6, 16))

        tk.Label(form, text="Password", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.password_entry = ttk.Entry(form, width=30, show="*")
        self.password_entry.pack(fill="x", pady=(6, 20))

        btns = tk.Frame(form, bg=BG_PANEL)
        btns.pack(fill="x", pady=10)

        ttk.Button(
            btns,
            text="Login",
            command=self.handle_login,
            style="Accent.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ttk.Button(
            btns,
            text="Create Account",
            command=self.handle_register,
            style="Secondary.TButton",
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            form,
            text=(
                "Local prototype note:\n"
                "Accounts, listings, and inbox messages are stored in SQLite "
                "for demo purposes."
            ),
            bg=BG_PANEL,
            fg=TEXT_MUTED,
            font=("Consolas", 9),
            justify="left",
        ).pack(anchor="w", pady=(30, 0))

    def handle_register(self) -> None:
        """Create a new account but do not force the user into posting."""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password:
            messagebox.showwarning("Missing fields", "Enter a username and password.")
            return

        ok, msg = create_user(username, password)
        if ok:
            messagebox.showinfo("Success", f"{msg}\nYou can now log in.")
            self.username_entry.delete(0, "end")
            self.password_entry.delete(0, "end")
        else:
            messagebox.showerror("Error", msg)

    def handle_login(self) -> None:
        """Authenticate the user, then send them to the main hub screen."""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        ok, user_id = authenticate_user(username, password)

        if not ok or user_id is None:
            messagebox.showerror("Login failed", "Invalid username or password.")
            return

        self.current_user_id = user_id
        self.current_username = username
        self.show_main_hub()

    # -------------------------------------------------
    # Main hub screen
    # -------------------------------------------------
    def show_main_hub(self) -> None:
        self.clear_screen()
        self.create_neon_background()

        self.build_topbar("CYBER XCHANGE // MAIN HUB")

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=28, pady=20)

        hero = tk.Frame(outer, bg=BG_MAIN)
        hero.pack(fill="x", pady=(0, 20))

        left = tk.Frame(hero, bg=BG_MAIN)
        left.pack(side="left", fill="both", expand=True)

        right = tk.Frame(
            hero,
            bg=BG_PANEL,
            highlightbackground=NEON_PURPLE,
            highlightthickness=1,
        )
        right.pack(side="right", fill="y", padx=(20, 0))
        right.configure(width=350)
        right.pack_propagate(False)

        logo = self.load_logo_widget(left, size=(250, 250))
        logo.pack(anchor="w", pady=(0, 10))

        tk.Label(
            left,
            text=f"Welcome back, {self.current_username}",
            bg=BG_MAIN,
            fg=NEON_BLUE,
            font=("Consolas", 26, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            left,
            text=(
                "Choose how you want to interact with the trading network. "
                "Upload new trade posts, browse categories, check your inbox, "
                "or manage your profile and listings."
            ),
            bg=BG_MAIN,
            fg=TEXT_SOFT,
            font=("Consolas", 12),
            wraplength=620,
            justify="left",
        ).pack(anchor="w")

        tk.Label(
            right,
            text="TRADE RULES",
            bg=BG_PANEL,
            fg=NEON_PINK,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", padx=18, pady=(18, 12))

        rules = [
            "• Trading only",
            "• No sale buttons",
            "• Use value as a reference point",
            "• Compare fairness before messaging",
            "• Browse by category for smoother discovery",
        ]
        for rule in rules:
            tk.Label(
                right,
                text=rule,
                bg=BG_PANEL,
                fg=TEXT_MAIN,
                font=("Consolas", 10),
                anchor="w",
            ).pack(anchor="w", padx=18, pady=3)

        grid = tk.Frame(outer, bg=BG_MAIN)
        grid.pack(fill="both", expand=True)

        upload_card = self.make_card_button(
            grid,
            "UPLOAD TRADE",
            "Create a new barter listing with photo, condition, estimated value, and desired trade value.",
            NEON_BLUE,
            self.show_upload_screen,
        )
        browse_card = self.make_card_button(
            grid,
            "BROWSE TRADES",
            "View other users' postings by category and compare item trade values.",
            NEON_GREEN,
            self.show_browse_screen,
        )
        inbox_card = self.make_card_button(
            grid,
            "MESSAGING INBOX",
            "Read messages from other traders and keep track of incoming trade interest.",
            NEON_PINK,
            self.show_inbox_screen,
        )
        profile_card = self.make_card_button(
            grid,
            "MY PROFILE",
            "View your current identity in the app and review the trade posts you have uploaded.",
            NEON_PURPLE,
            self.show_profile_screen,
        )

        upload_card.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        browse_card.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        inbox_card.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        profile_card.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)

    # -------------------------------------------------
    # Upload screen
    # -------------------------------------------------
    def show_upload_screen(self) -> None:
        self.clear_screen()
        self.create_neon_background()
        self.build_topbar("UPLOAD TRADE", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        form = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        form.pack(fill="both", expand=True)

        inner = tk.Frame(form, bg=BG_CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=26)

        tk.Label(
            inner,
            text="CREATE A TRADE LISTING",
            bg=BG_CARD,
            fg=NEON_BLUE,
            font=("Consolas", 22, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 20))

        tk.Label(inner, text="Item Title", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.title_entry = ttk.Entry(inner)
        self.title_entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 15), pady=(0, 14))

        tk.Label(inner, text="Category", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=3, column=0, sticky="w", pady=(0, 6))
        self.category_combo = ttk.Combobox(
            inner,
            values=["Electronics", "Clothing", "Collectibles", "Home", "Tools", "Gaming", "Other"],
            state="readonly",
        )
        self.category_combo.grid(row=4, column=0, sticky="ew", padx=(0, 15), pady=(0, 14))
        self.category_combo.set("Other")

        tk.Label(inner, text="Condition", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=3, column=1, sticky="w", pady=(0, 6))
        self.condition_combo = ttk.Combobox(
            inner,
            values=["New", "Like New", "Good", "Fair", "Used"],
            state="readonly",
        )
        self.condition_combo.grid(row=4, column=1, sticky="ew", pady=(0, 14))
        self.condition_combo.set("Good")

        tk.Label(inner, text="Estimated Value ($)", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=5, column=0, sticky="w", pady=(0, 6))
        self.estimated_entry = ttk.Entry(inner)
        self.estimated_entry.grid(row=6, column=0, sticky="ew", padx=(0, 15), pady=(0, 14))

        tk.Label(inner, text="Desired Trade Value ($)", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=5, column=1, sticky="w", pady=(0, 6))
        self.desired_entry = ttk.Entry(inner)
        self.desired_entry.grid(row=6, column=1, sticky="ew", pady=(0, 14))

        tk.Label(inner, text="Description / Trade Notes", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=7, column=0, sticky="w", pady=(0, 6))
        self.description_text = tk.Text(
            inner,
            height=8,
            wrap="word",
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            font=("Consolas", 10),
        )
        self.description_text.grid(row=8, column=0, columnspan=2, sticky="ew", padx=(0, 15), pady=(0, 16))

        upload_frame = tk.Frame(
            inner,
            bg=BG_ALT,
            highlightbackground=NEON_PINK,
            highlightthickness=1,
        )
        upload_frame.grid(row=1, column=2, rowspan=8, sticky="nsew")

        self.preview_label = tk.Label(
            upload_frame,
            text="No photo selected",
            bg=BG_INPUT,
            fg=TEXT_SOFT,
            width=28,
            height=14,
            relief="flat",
            font=("Consolas", 10),
        )
        self.preview_label.pack(pady=(14, 12), padx=12, fill="both", expand=False)

        ttk.Button(
            upload_frame,
            text="Upload Photo",
            command=self.select_photo,
            style="Secondary.TButton",
        ).pack(fill="x", padx=12, pady=(0, 10))

        ttk.Button(
            upload_frame,
            text="Save Listing",
            command=self.save_listing,
            style="Accent.TButton",
        ).pack(fill="x", padx=12, pady=(0, 10))

        self.feedback_var = tk.StringVar(value="Add your trade item details, then save.")
        tk.Label(
            inner,
            textvariable=self.feedback_var,
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 10, "italic"),
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(12, 0))

        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)
        inner.columnconfigure(2, weight=1)

    def select_photo(self) -> None:
        """Open a file picker and preview the selected image if Pillow exists."""
        file_path = filedialog.askopenfilename(
            title="Choose item photo",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif *.webp")],
        )
        if not file_path:
            return

        self.selected_photo_path = file_path

        if Image and ImageTk:
            image = Image.open(file_path)
            image.thumbnail((260, 260))
            self.preview_photo = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_photo, text="")
        else:
            self.preview_label.configure(text=os.path.basename(file_path))

    def save_listing(self) -> None:
        """Validate and save a barter listing, then return the user to the hub."""
        if self.current_user_id is None:
            messagebox.showerror("Error", "No user logged in.")
            return

        title = self.title_entry.get().strip()
        category = self.category_combo.get().strip()
        condition = self.condition_combo.get().strip()
        description = self.description_text.get("1.0", "end").strip()
        estimated_raw = self.estimated_entry.get().strip()
        desired_raw = self.desired_entry.get().strip()

        if not title or not estimated_raw or not desired_raw:
            messagebox.showwarning(
                "Missing information",
                "Title, estimated value, and desired trade value are required.",
            )
            return

        try:
            estimated_value = float(estimated_raw)
            desired_value = float(desired_raw)
        except ValueError:
            messagebox.showerror(
                "Invalid values",
                "Estimated value and desired trade value must be numbers.",
            )
            return

        save_item(
            self.current_user_id,
            title,
            category,
            condition,
            description,
            estimated_value,
            desired_value,
            self.selected_photo_path,
        )

        label, score = fairness_score(estimated_value, desired_value)
        self.feedback_var.set(f"Listing saved. Trade balance reference: {label} ({score}%).")
        messagebox.showinfo("Saved", f"Listing saved.\nTrade balance reference: {label} ({score}%).")
        self.show_main_hub()

    # -------------------------------------------------
    # Browse screen
    # -------------------------------------------------
    def show_browse_screen(self) -> None:
        self.clear_screen()
        self.create_neon_background()
        self.build_topbar("BROWSE TRADES", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        top = tk.Frame(outer, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))

        tk.Label(
            top,
            text="Category Filter",
            bg=BG_MAIN,
            fg=NEON_GREEN,
            font=("Consolas", 11, "bold"),
        ).pack(side="left", padx=(0, 10))

        self.browse_category_combo = ttk.Combobox(
            top,
            values=["All", "Electronics", "Clothing", "Collectibles", "Home", "Tools", "Gaming", "Other"],
            state="readonly",
            width=18,
        )
        self.browse_category_combo.pack(side="left")
        self.browse_category_combo.set("All")

        ttk.Button(
            top,
            text="Load Category",
            command=self.refresh_browse_results,
            style="Accent.TButton",
        ).pack(side="left", padx=10)

        browse_frame = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_GREEN,
            highlightthickness=1,
        )
        browse_frame.pack(fill="both", expand=True)

        self.browse_text = tk.Text(
            browse_frame,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.browse_text.pack(fill="both", expand=True, padx=16, pady=16)
        self.browse_text.config(state="disabled")

        bottom = tk.Frame(outer, bg=BG_MAIN)
        bottom.pack(fill="x", pady=(14, 0))

        tk.Label(
            bottom,
            text="To start trade conversations, note the trader username and use Inbox to send a message.",
            bg=BG_MAIN,
            fg=TEXT_MUTED,
            font=("Consolas", 10),
        ).pack(side="left")

        self.refresh_browse_results()

    def refresh_browse_results(self) -> None:
        """Load browse results based on the selected category."""
        if self.current_user_id is None:
            return

        category = self.browse_category_combo.get().strip() if hasattr(self, "browse_category_combo") else "All"
        items = get_other_items_by_category(self.current_user_id, category)

        self.browse_text.config(state="normal")
        self.browse_text.delete("1.0", "end")

        if not items:
            self.browse_text.insert(
                "end",
                "No trade posts found for this category yet.\n"
                "Try another category or create a second account for demo browsing."
            )
            self.browse_text.config(state="disabled")
            return

        for item in items:
            item_id, owner_id, owner_username, title, item_category, condition, description, estimated_value, desired_trade_value, photo_path, created_at = item
            label, score = fairness_score(estimated_value, desired_trade_value)

            block = (
                f"{title}\n"
                f"Trader: {owner_username}\n"
                f"Category: {item_category}\n"
                f"Condition: {condition}\n"
                f"Estimated Value: ${estimated_value:,.2f}\n"
                f"Desired Trade Value: ${desired_trade_value:,.2f}\n"
                f"Trade Balance Reference: {label} ({score}%)\n"
                f"Notes: {description or 'No description provided.'}\n"
                f"Photo: {photo_path or 'No photo uploaded'}\n"
                f"{'-' * 64}\n"
            )
            self.browse_text.insert("end", block)

        self.browse_text.config(state="disabled")

    # -------------------------------------------------
    # Inbox screen
    # -------------------------------------------------
    def show_inbox_screen(self) -> None:
        self.clear_screen()
        self.create_neon_background()
        self.build_topbar("MESSAGING INBOX", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        left = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_PINK,
            highlightthickness=1,
        )
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        right.pack(side="right", fill="y", padx=(10, 0))
        right.configure(width=360)
        right.pack_propagate(False)

        inbox_wrap = tk.Frame(left, bg=BG_CARD)
        inbox_wrap.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            inbox_wrap,
            text="INCOMING TRADE MESSAGES",
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self.inbox_text = tk.Text(
            inbox_wrap,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.inbox_text.pack(fill="both", expand=True)
        self.inbox_text.config(state="disabled")

        composer = tk.Frame(right, bg=BG_PANEL)
        composer.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            composer,
            text="SEND MESSAGE",
            bg=BG_PANEL,
            fg=NEON_BLUE,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        tk.Label(composer, text="Send To", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_user_combo = ttk.Combobox(composer, state="readonly")
        users = get_all_users_except(self.current_user_id) if self.current_user_id is not None else []
        self.user_lookup = {username: user_id for user_id, username in users}
        self.message_user_combo["values"] = list(self.user_lookup.keys())
        if users:
            self.message_user_combo.set(users[0][1])
        self.message_user_combo.pack(fill="x", pady=(6, 14))

        tk.Label(composer, text="Subject", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_subject_entry = ttk.Entry(composer)
        self.message_subject_entry.pack(fill="x", pady=(6, 14))

        tk.Label(composer, text="Message", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_body_text = tk.Text(
            composer,
            height=12,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.message_body_text.pack(fill="both", expand=True, pady=(6, 14))

        ttk.Button(
            composer,
            text="Send Message",
            command=self.handle_send_message,
            style="Accent.TButton",
        ).pack(fill="x")

        self.refresh_inbox()

    def refresh_inbox(self) -> None:
        """Reload incoming messages for the logged-in user."""
        if self.current_user_id is None:
            return

        messages = get_inbox_messages(self.current_user_id)

        self.inbox_text.config(state="normal")
        self.inbox_text.delete("1.0", "end")

        if not messages:
            self.inbox_text.insert(
                "end",
                "No messages yet.\n\nWhen traders contact you, their messages will appear here."
            )
            self.inbox_text.config(state="disabled")
            return

        for msg in messages:
            msg_id, sender_username, subject, body, created_at, is_read = msg
            block = (
                f"From: {sender_username}\n"
                f"Subject: {subject}\n"
                f"Sent: {created_at[:19].replace('T', ' ')}\n"
                f"Message:\n{body}\n"
                f"{'-' * 64}\n"
            )
            self.inbox_text.insert("end", block)

        self.inbox_text.config(state="disabled")

    def handle_send_message(self) -> None:
        """Send a local inbox message to another user."""
        if self.current_user_id is None:
            messagebox.showerror("Error", "No user logged in.")
            return

        username = self.message_user_combo.get().strip()
        subject = self.message_subject_entry.get().strip()
        body = self.message_body_text.get("1.0", "end").strip()

        if not username:
            messagebox.showwarning("Missing recipient", "Choose a user to message.")
            return

        if not subject or not body:
            messagebox.showwarning("Missing information", "Subject and message are required.")
            return

        receiver_id = self.user_lookup.get(username)
        if receiver_id is None:
            messagebox.showerror("Error", "Selected user not found.")
            return

        send_message(self.current_user_id, receiver_id, subject, body)

        self.message_subject_entry.delete(0, "end")
        self.message_body_text.delete("1.0", "end")
        messagebox.showinfo("Sent", f"Message sent to {username}.")
        self.refresh_inbox()

    # -------------------------------------------------
    # Profile screen
    # -------------------------------------------------
    def show_profile_screen(self) -> None:
        self.clear_screen()
        self.create_neon_background()
        self.build_topbar("MY PROFILE", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        top_card = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_PURPLE,
            highlightthickness=1,
        )
        top_card.pack(fill="x", pady=(0, 16))

        tk.Label(
            top_card,
            text=self.current_username,
            bg=BG_PANEL,
            fg=NEON_BLUE,
            font=("Consolas", 24, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 8))

        user_items = get_user_items(self.current_user_id) if self.current_user_id is not None else []

        tk.Label(
            top_card,
            text=f"Active Trade Posts: {len(user_items)}",
            bg=BG_PANEL,
            fg=NEON_GREEN,
            font=("Consolas", 12, "bold"),
        ).pack(anchor="w", padx=20, pady=(0, 18))

        listings_card = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        listings_card.pack(fill="both", expand=True)

        tk.Label(
            listings_card,
            text="MY UPLOADED LISTINGS",
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", padx=18, pady=(18, 12))

        profile_text = tk.Text(
            listings_card,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        profile_text.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        if not user_items:
            profile_text.insert(
                "end",
                "You do not have any uploaded trade listings yet.\n\nUse Upload Trade from the main hub to create one."
            )
        else:
            for item in user_items:
                item_id, title, category, condition, description, estimated_value, desired_trade_value, photo_path, created_at = item
                label, score = fairness_score(estimated_value, desired_trade_value)

                block = (
                    f"{title}\n"
                    f"Category: {category}\n"
                    f"Condition: {condition}\n"
                    f"Estimated Value: ${estimated_value:,.2f}\n"
                    f"Desired Trade Value: ${desired_trade_value:,.2f}\n"
                    f"Trade Balance Reference: {label} ({score}%)\n"
                    f"Notes: {description or 'No description provided.'}\n"
                    f"Photo: {photo_path or 'No photo uploaded'}\n"
                    f"Created: {created_at[:19].replace('T', ' ')}\n"
                    f"{'-' * 64}\n"
                )
                profile_text.insert("end", block)

        profile_text.config(state="disabled")


# -------------------------------------------------
# App entry point
# -------------------------------------------------
if __name__ == "__main__":
    init_db()
    app = SilkRouteApp()
    app.mainloop()
