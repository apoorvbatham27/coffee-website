
import os
import re
from functools import wraps
from datetime import date, datetime
from decimal import Decimal
from jinja2 import ChoiceLoader, FileSystemLoader
import mysql.connector
from mysql.connector import Error
from flask import (
    Flask, request, jsonify, session,
    send_from_directory, render_template
)
from werkzeug.security import generate_password_hash, check_password_hash



# =========================================================
# 1. FLASK CONFIGURATION
# =========================================================



app = Flask(__name__)

@app.route("/images/<path:filename>")
def serve_product_image(filename):
    return send_from_directory(
        os.path.join(app.root_path, "images"),
        filename
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app.jinja_loader = ChoiceLoader([
    FileSystemLoader(TEMPLATE_DIR),
    FileSystemLoader(BASE_DIR)
])

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "coffycup-local-development-secret-2026"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_REFRESH_EACH_REQUEST=True,
    PERMANENT_SESSION_LIFETIME=__import__("datetime").timedelta(days=7)
)

# =========================================================
# 2. MYSQL CONFIGURATION
# =========================================================

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "coffycup_database")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")


def get_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )


def query_db(sql, values=None, fetchone=False, commit=False):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, values or ())

        if commit:
            result = {
                "success": True,
                "insert_id": cursor.lastrowid,
                "affected_rows": cursor.rowcount
            }
            connection.commit()
            return result

        if cursor.with_rows:
            return cursor.fetchone() if fetchone else cursor.fetchall()

        connection.commit()
        return {
            "success": True,
            "affected_rows": cursor.rowcount
        }

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def json_safe(value):
    if isinstance(value, list):
        return [json_safe(item) for item in value]

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def respond(data, status=200):
    return jsonify(json_safe(data)), status


def get_json():
    return request.get_json(silent=True) or {}


def clean_text(value):
    return "" if value is None else str(value).strip()


def positive_number(value, field_name):
    try:
        number = int(value)
        if number <= 0:
            raise ValueError
        return number
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a positive number.")


# =========================================================
# 3. LOGIN PROTECTION
# =========================================================

def user_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return respond({"error": "Please log in first."}, 401)
        return function(*args, **kwargs)
    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return respond({"error": "Admin login required."}, 401)
        return function(*args, **kwargs)
    return wrapper


# =========================================================
# 4. DATABASE AND TABLE SETUP
# =========================================================

def initialize_database():
    if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
        raise ValueError("Invalid database name.")

    connection = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD
    )
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    finally:
        cursor.close()
        connection.close()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(150) NOT NULL UNIQUE,
                phone VARCHAR(20),
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                cat VARCHAR(100) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                img VARCHAR(500) DEFAULT '',
                description TEXT,
                sold INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                name VARCHAR(100) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                email VARCHAR(150),
                addr TEXT NOT NULL,
                city VARCHAR(100),
                pincode VARCHAR(15),
                prod VARCHAR(150) NOT NULL,
                product_id INT NULL,
                qty INT NOT NULL,
                total DECIMAL(10,2) NOT NULL,
                payment_method VARCHAR(50)
                    DEFAULT 'Cash on Delivery',
                status VARCHAR(30) NOT NULL DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE SET NULL,
                FOREIGN KEY (product_id) REFERENCES products(id)
                    ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(150),
                msg TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE SET NULL
            )
        """)

        connection.commit()

        cursor.execute(
            "SELECT id, password_hash FROM admins WHERE username = %s",
            (ADMIN_USERNAME,)
        )
        admin = cursor.fetchone()

        if admin is None:
            cursor.execute("""
                INSERT INTO admins (username, password_hash)
                VALUES (%s, %s)
            """, (
                ADMIN_USERNAME,
                generate_password_hash(ADMIN_PASSWORD)
            ))
            connection.commit()

        elif not check_password_hash(admin[1], ADMIN_PASSWORD):
            cursor.execute("""
                UPDATE admins
                SET password_hash = %s
                WHERE username = %s
            """, (
                generate_password_hash(ADMIN_PASSWORD),
                ADMIN_USERNAME
            ))
            connection.commit()

    finally:
        cursor.close()
        connection.close()


# =========================================================
# 5. JINJA USER PAGES
# =========================================================

@app.route("/")
@app.route("/home.html")
def home():
    products = query_db("SELECT * FROM products")

    brewed_products = []
    ready_to_drink_products = []

    for product in products:
        category = str(product.get("cat", "")).strip().lower()

        if category == "coffee":
            brewed_products.append(product)

        elif category == "ready to drink":
            ready_to_drink_products.append(product)

    return render_template(
        "home.html",
        products=products,
        brewed_products=brewed_products,
        ready_to_drink_products=ready_to_drink_products,
        current_user=session.get("user_name")
    )


@app.route("/gifting.html")
def gifting():
    current_user = None

    if session.get("user_id"):
        current_user = query_db("""
            SELECT id, name, email, phone
            FROM users
            WHERE id = %s
        """, (session["user_id"],), fetchone=True)

        if current_user is None:
            session.clear()

    gifting_products = query_db("""
        SELECT id, name, cat, price, img, description
        FROM products
        WHERE LOWER(cat) LIKE '%gift%'
           OR LOWER(cat) LIKE '%hamper%'
        ORDER BY id DESC
    """)

    return render_template(
        "gifting.html",
        current_user=current_user,
        gifting_products=gifting_products
    )


@app.route("/login.html")
def login_page():
    return render_template("admin/login-form.html")


@app.route("/register.html")
def register_page():
    return render_template("admin/register-form.html")
    
@app.route("/<path:filename>")
def user_pages(filename):
    if filename == "admin" or filename.startswith("admin/"):
        return "Not found", 404
        
   
    base_path = os.path.realpath(BASE_DIR)
    full_path = os.path.realpath(os.path.join(BASE_DIR, filename))

    if not full_path.startswith(base_path + os.sep):
        return "Invalid path", 400

    if not os.path.isfile(full_path):
        return "Page not found", 404

    return send_from_directory(BASE_DIR, filename)


# =========================================================
# 6. ADMIN PAGES
# =========================================================

@app.route("/admin/")
def admin_home():
    return render_template("admin/login-form.html")

@app.route("/admin/<path:filename>")
def admin_pages(filename):
    admin_dir = os.path.realpath(
        os.path.join(BASE_DIR, "admin")
    )
    full_path = os.path.realpath(
        os.path.join(admin_dir, filename)
    )

    if not full_path.startswith(admin_dir + os.sep):
        return "Invalid path", 400

    if not os.path.isfile(full_path):
        return "Admin page not found", 404

    if filename.endswith(".html"):
        return render_template(f"admin/{filename}")

    return send_from_directory(admin_dir, filename)
# =========================================================
# 7. USER REGISTRATION AND LOGIN
# =========================================================

@app.route("/api/register", methods=["POST"])
def register():
    data = get_json()

    name = clean_text(data.get("name"))
    email = clean_text(data.get("email")).lower()
    phone = clean_text(data.get("phone"))
    password = str(data.get("password") or "")

    if not name or not email or not password:
        return respond({
            "error": "Name, email and password are required."
        }, 400)

    if len(name) > 100 or len(email) > 150:
        return respond({"error": "Name or email is too long."}, 400)

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return respond({"error": "Enter a valid email address."}, 400)

    if len(password) < 8:
        return respond({
            "error": "Password must contain at least 8 characters."
        }, 400)

    try:
        result = query_db("""
            INSERT INTO users (name, email, phone, password_hash)
            VALUES (%s, %s, %s, %s)
        """, (
            name,
            email,
            phone or None,
            generate_password_hash(password)
        ), commit=True)

        return respond({
            "message": "Registration successful.",
            "user_id": result["insert_id"]
        }, 201)

    except Error as error:
        if error.errno == 1062:
            return respond({
                "error": "An account with this email already exists."
            }, 409)

        app.logger.exception("Registration failed")
        return respond({"error": "Registration failed."}, 500)


@app.route("/api/login", methods=["POST"])
def login():
    data = get_json()

    email = clean_text(data.get("email")).lower()
    password = str(data.get("password") or "")

    user = query_db(
        "SELECT * FROM users WHERE email = %s",
        (email,),
        fetchone=True
    )

    if not user or not check_password_hash(
        user["password_hash"], password
    ):
        return respond({"error": "Invalid email or password."}, 401)

    session.clear()
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]

    return respond({
        "message": "Login successful.",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"]
        }
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return respond({"message": "Logged out successfully."})


@app.route("/api/me")
@user_required
def current_user():
    user = query_db("""
        SELECT id, name, email, phone
        FROM users
        WHERE id = %s
    """, (session["user_id"],), fetchone=True)

    if not user:
        session.clear()
        return respond({"error": "User account not found."}, 404)

    return respond({"user": user})


# =========================================================
# 8. PUBLIC PRODUCT APIs
# =========================================================

@app.route("/api/products", methods=["GET"])
def get_products():
    category = clean_text(request.args.get("category"))

    if category:
        products = query_db("""
            SELECT id, name, cat, price, img, description, sold
            FROM products
            WHERE cat = %s
            ORDER BY id DESC
        """, (category,))
    else:
        products = query_db("""
            SELECT id, name, cat, price, img, description, sold
            FROM products
            ORDER BY id DESC
        """)

    return respond({"products": products})


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = query_db("""
        SELECT id, name, cat, price, img, description, sold
        FROM products
        WHERE id = %s
    """, (product_id,), fetchone=True)

    if not product:
        return respond({"error": "Product not found."}, 404)

    return respond({"product": product})


# =========================================================
# 9. ADMIN PRODUCT MANAGEMENT
# =========================================================

@app.route("/api/admin/products", methods=["POST"])
@admin_required
def add_product():
    data = get_json()

    name = clean_text(data.get("name"))
    category = clean_text(data.get("cat") or data.get("category"))
    image = clean_text(data.get("img") or data.get("image"))
    description = clean_text(data.get("description"))

    try:
        price = Decimal(str(data.get("price")))
        if not price.is_finite() or price <= 0 or price > Decimal("99999999"):
            raise ValueError
    except (TypeError, ValueError, ArithmeticError):
        return respond({"error": "Enter a valid product price."}, 400)

    if not name or not category:
        return respond({
            "error": "Product name and category are required."
        }, 400)

    try:
        result = query_db("""
            INSERT INTO products (name, cat, price, img, description)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            name, category, price, image, description
        ), commit=True)

        return respond({
            "message": "Product added successfully.",
            "product_id": result["insert_id"]
        }, 201)

    except Error:
        app.logger.exception("Product creation failed")
        return respond({"error": "Could not add product."}, 500)


@app.route("/api/admin/products/<int:product_id>", methods=["PUT"])
@admin_required
def update_product(product_id):
    data = get_json()

    existing = query_db(
        "SELECT * FROM products WHERE id = %s",
        (product_id,),
        fetchone=True
    )

    if not existing:
        return respond({"error": "Product not found."}, 404)

    name = clean_text(data.get("name", existing["name"]))
    category = clean_text(
        data.get("cat", data.get("category", existing["cat"]))
    )
    image = clean_text(
        data.get("img", data.get("image", existing["img"] or ""))
    )
    description = clean_text(
        data.get("description", existing["description"] or "")
    )

    try:
        price = Decimal(str(data.get("price", existing["price"])))
        if not price.is_finite() or price <= 0 or price > Decimal("99999999"):
            raise ValueError
    except (TypeError, ValueError, ArithmeticError):
        return respond({"error": "Enter a valid product price."}, 400)

    if not name or not category:
        return respond({
            "error": "Product name and category are required."
        }, 400)

    query_db("""
        UPDATE products
        SET name = %s, cat = %s, price = %s,
            img = %s, description = %s
        WHERE id = %s
    """, (
        name, category, price, image, description, product_id
    ), commit=True)

    return respond({"message": "Product updated successfully."})


@app.route("/api/admin/products/<int:product_id>", methods=["DELETE"])
@admin_required
def delete_product(product_id):
    result = query_db(
        "DELETE FROM products WHERE id = %s",
        (product_id,),
        commit=True
    )

    if result["affected_rows"] == 0:
        return respond({"error": "Product not found."}, 404)

    return respond({"message": "Product deleted successfully."})


# =========================================================
# 10. PLACE ORDER
# =========================================================

@app.route("/api/orders", methods=["POST"])
@user_required
def place_order():

    data = get_json() 
     
    name = clean_text(data.get("name"))
    phone = clean_text(data.get("phone"))
    address = clean_text(data.get("addr") or data.get("address"))
    city = clean_text(data.get("city"))
    pincode = clean_text(data.get("pincode"))
    email = clean_text(data.get("email"))
    payment_method = clean_text(
        data.get("payment_method") or "Cash on Delivery"
    )

    try:
        product_id = positive_number(data.get("product_id"), "Product ID")
        quantity = positive_number(
            data.get("qty") or data.get("quantity"), "Quantity"
        )
    except ValueError as error:
        return respond({"error": str(error)}, 400)

    if not name or not phone or not address:
        return respond({
            "error": "Name, phone and delivery address are required."
        }, 400)

    if not re.fullmatch(r"[0-9+\-\s()]{7,20}", phone):
        return respond({"error": "Enter a valid phone number."}, 400)

    if quantity > 100:
        return respond({"error": "Maximum order quantity is 100."}, 400)

    if payment_method != "Cash on Delivery":
        return respond({
            "error": "Only Cash on Delivery is currently supported."
        }, 400)

    # Always calculate the price using the database value.
    product = query_db("""
        SELECT id, name, price
        FROM products
        WHERE id = %s
    """, (product_id,), fetchone=True)

    if not product:
        return respond({"error": "Product not found."}, 404)

    total = Decimal(str(product["price"])) * quantity

    try:
        result = query_db("""
            INSERT INTO orders
                (user_id, name, phone, email, addr, city, pincode,
                 prod, product_id, qty, total, payment_method, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, 'Pending')
        """, (
            session.get("user_id"),
            name, phone, email or None, address,
            city or None, pincode or None,
            product["name"], product_id, quantity,
            total, payment_method
        ), commit=True)

        query_db("""
            UPDATE products
            SET sold = sold + %s
            WHERE id = %s
        """, (quantity, product_id), commit=True)

        return respond({
            "message": "Order placed successfully.",
            "order_id": result["insert_id"],
            "total": total,
            "status": "Pending"
        }, 201)

    except Error:
        app.logger.exception("Order creation failed")
        return respond({"error": "Could not place the order."}, 500)


# =========================================================
# 11. USER - MY ORDERS
# =========================================================

@app.route("/api/my-orders", methods=["GET"])
@user_required
def my_orders():
    orders = query_db("""
        SELECT
            o.id,
            o.prod,
            o.product_id,
            o.qty,
            o.total,
            o.status,
            o.payment_method,
            o.created_at,
            p.img AS img
        FROM orders o
        LEFT JOIN products p
            ON o.product_id = p.id
        WHERE o.user_id = %s
        ORDER BY o.created_at DESC
    """, (session["user_id"],))

    return respond({"orders": orders})

# =========================================================
# 12. ADMIN - ORDERS AND STATUS UPDATES
# =========================================================

@app.route("/api/admin/orders", methods=["GET"])
@admin_required
def admin_orders():
    orders = query_db("""
        SELECT id, user_id, name, phone, email, addr, city, pincode,
               prod, product_id, qty, total, payment_method,
               status, created_at
        FROM orders
        ORDER BY created_at DESC
    """)

    return respond({"orders": orders})


@app.route("/api/admin/orders/<int:order_id>", methods=["PUT"])
@admin_required
def update_order_status(order_id):
    data = get_json()
    status = clean_text(data.get("status"))

    allowed_statuses = {
        "Pending", "Confirmed", "Processing",
        "Shipped", "Delivered", "Cancelled"
    }

    if status not in allowed_statuses:
        return respond({"error": "Invalid order status."}, 400)

    existing = query_db(
        "SELECT id FROM orders WHERE id = %s",
        (order_id,),
        fetchone=True
    )

    if not existing:
        return respond({"error": "Order not found."}, 404)

    query_db(
        "UPDATE orders SET status = %s WHERE id = %s",
        (status, order_id),
        commit=True
    )

    return respond({
        "message": "Order status updated.",
        "status": status
    })
    
    
# =========================================================
#  ADMIN - MANAGE USERS
# =========================================================

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_users():
    users = query_db("""
        SELECT id, name, email, phone, created_at
        FROM users
        ORDER BY created_at DESC
    """)

    return respond({"users": users})



# =========================================================
# 13. SUBMIT FEEDBACK
# =========================================================


@app.route("/api/feedback", methods=["POST"])
@user_required
def submit_feedback():
    data = get_json()

    name = clean_text(data.get("name"))
    email = clean_text(data.get("email"))
    message = clean_text(data.get("msg") or data.get("message"))
    rating = data.get("rating")

    if not name or not message:
        return respond({
            "error": "Name and feedback message are required."
        }, 400)

    if len(name) > 100 or len(email) > 150:
        return respond({
            "error": "Name or email is too long."
        }, 400)

    try:
        rating = int(rating)

        if rating < 1 or rating > 5:
            raise ValueError

    except (TypeError, ValueError):
        return respond({
            "error": "Please select a rating from 1 to 5."
        }, 400)

    try:
        result = query_db("""
            INSERT INTO feedback (user_id, name, email, msg, rating)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            session.get("user_id"),
            name,
            email or None,
            message,
            rating
        ), commit=True)

        return respond({
            "message": "Thank you! Your feedback was submitted successfully.",
            "feedback_id": result["insert_id"]
        }, 201)

    except Error:
        app.logger.exception("Feedback submission failed")
        return respond({
            "error": "Could not submit feedback."
        }, 500)


# =========================================================
# 14. ADMIN - VIEW FEEDBACK
# =========================================================


@app.route("/api/admin/feedback", methods=["GET"])
@admin_required
def admin_feedback():
    feedback_items = query_db("""
        SELECT id, name, email, msg, rating, created_at
        FROM feedback
        ORDER BY created_at DESC
    """)

    return respond({"feedback": feedback_items})


# =========================================================
# 15. ADMIN LOGIN AND LOGOUT
# =========================================================

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = get_json()

    username = clean_text(data.get("username"))
    password = str(data.get("password") or "")

    admin = query_db(
        "SELECT * FROM admins WHERE username = %s",
        (username,),
        fetchone=True
    )

    if not admin or not check_password_hash(
        admin["password_hash"], password
    ):
        return respond({"error": "Invalid admin credentials."}, 401)

    session.clear()
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]

    return respond({
        "message": "Admin login successful.",
        "username": admin["username"]
    })


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_username", None)

    return respond({"message": "Admin logged out successfully."})


# =========================================================
# 16. ADMIN DASHBOARD STATISTICS
# =========================================================

@app.route("/api/admin/dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    product_count = query_db(
        "SELECT COUNT(*) AS total FROM products",
        fetchone=True
    )

    order_count = query_db(
        "SELECT COUNT(*) AS total FROM orders",
        fetchone=True
    )

    user_count = query_db(
        "SELECT COUNT(*) AS total FROM users",
        fetchone=True
    )

    feedback_count = query_db(
        "SELECT COUNT(*) AS total FROM feedback",
        fetchone=True
    )

    revenue = query_db("""
        SELECT COALESCE(SUM(total), 0) AS total
        FROM orders
        WHERE status = 'Delivered'
    """, fetchone=True)

    return respond({
        "total_products": product_count["total"],
        "total_orders": order_count["total"],
        "total_users": user_count["total"],
        "total_feedback": feedback_count["total"],
        "delivered_revenue": revenue["total"]
    })


# =========================================================
# 17. ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return respond({"error": "API endpoint not found."}, 404)

    return "Page not found", 404


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Unexpected server error")

    if request.path.startswith("/api/"):
        return respond({
            "error": "An internal server error occurred."
        }, 500)

    return "An internal server error occurred.", 500


# =========================================================
# 18. START APPLICATION
# =========================================================

if __name__ == "__main__":
    try:
        initialize_database()
        print("CoffyCup database is ready.")
        print("Starting CoffyCup Flask application...")
        print(f"Admin username: {ADMIN_USERNAME}")

        app.run(debug=True)

    except (Error, ValueError):
        app.logger.exception(
            "Could not initialize the database. "
            "Check your MySQL service and database credentials."
        )
