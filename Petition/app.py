from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pymongo import MongoClient, DESCENDING
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import os, requests, json, re, logging, secrets, io
from flask_mail import Mail, Message
import pytz
from markupsafe import Markup
from functools import wraps
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import fitz  # PyMuPDF
from PIL import Image
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

# Initialize app and config
load_dotenv()
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', secrets.token_hex(32)),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    OPENROUTER_API_KEY=os.environ.get('OPENROUTER_API_KEY'),
    UPLOAD_FOLDER=os.environ.get('UPLOAD_FOLDER', 'uploads'),
    MAIL_SERVER=os.environ.get('MAIL_SERVER', 'smtp.gmail.com'),
    MAIL_PORT=int(os.environ.get('MAIL_PORT', 587)),
    MAIL_USE_TLS=os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true',
    MAIL_USERNAME=os.environ.get('MAIL_USERNAME'),
    MAIL_PASSWORD=os.environ.get('MAIL_PASSWORD'),
    MAIL_DEFAULT_SENDER=os.environ.get('MAIL_DEFAULT_SENDER')
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create upload directory
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize extensions
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'

mail = Mail(app)
ist_tz = pytz.timezone('Asia/Kolkata')
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# MongoDB setup
mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/petition')
client = MongoClient(mongo_uri)
db = client.get_database()

# Helper functions
def get_current_ist():
    return datetime.now(ist_tz)

def format_ist_date(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt).astimezone(ist_tz)
    return dt.strftime('%d-%m-%Y %H:%M')

def requires_roles(*roles):
    def wrapper(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapped
    return wrapper

def extract_text_from_file(file_path):
    file_ext = file_path.rsplit(".", 1)[-1].lower()
    
    try:
        if file_ext == "txt":
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        elif file_ext == "pdf":
            doc = fitz.open(file_path)
            return "".join([page.get_text() for page in doc])
        elif file_ext in ["jpg", "jpeg", "png", "bmp"]:
            try:
                import pytesseract
                return pytesseract.image_to_string(Image.open(file_path))
            except ImportError:
                logger.warning("Pytesseract not installed. Image text extraction unavailable.")
                return "Image text extraction unavailable"
        return ""
    except Exception as e:
        logger.error(f"Error extracting text: {e}")
        return ""

def detect_language(text):
    if not text:
        return "en"
    tamil_chars = set("அஆஇஈஉஊஎஏஐஒஓஔகஙசஞடணதநபமயரலவழளறனஜஷஸஹ")
    return "ta" if len(set(text) & tamil_chars) > 5 else "en"

def get_department_id(name):
    if not name:
        return None
    department = db.departments.find_one({"name": name})
    if not department:
        keywords = generate_department_keywords(name)
        result = db.departments.insert_one({
            "name": name,
            "description": f"Department of {name}",
            "keywords": keywords
        })
        return result.inserted_id
    return department['_id']

def generate_department_keywords(name):
    api_key = app.config['OPENROUTER_API_KEY']
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set, using default keywords")
        return default_department_keywords(name)
        
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "qwen/qwen3-vl-30b-a3b-thinking",
                "messages": [
                    {"role": "system", "content": "You are an AI that generates keywords for government departments."},
                    {"role": "user", "content": f"Generate 15 specific keywords related to the '{name}' department. Include some in Tamil if possible. Respond with ONLY a valid JSON array of strings."}
                ],
                "response_format": {"type": "json_object"}
            },
            timeout=10 
        )
        
        if response.status_code == 200:
            result = response.json()["choices"][0]["message"]["content"]
            result = result.strip()
            # Clean markdown formatting
            for prefix in ["```json", "```"]:
                if result.startswith(prefix):
                    result = result[len(prefix):]
            if result.endswith("```"):
                result = result[:-3]
                
            keywords = json.loads(result.strip())
            if isinstance(keywords, list) and all(isinstance(k, str) for k in keywords):
                return keywords
    except Exception as e:
        logger.error(f"Error generating keywords: {e}")
        
    return default_department_keywords(name)

def default_department_keywords(name):
    default_keywords = {
        "Education": ["school", "education", "student", "teacher", "curriculum", "university", "college", "learning", "classroom", "academic"],
        "Health": ["health", "hospital", "doctor", "healthcare", "medical", "patient", "clinic", "treatment", "medicine", "disease"],
        "Infrastructure": ["road", "bridge", "building", "construction", "infrastructure", "maintenance", "repair", "public works", "facility", "development"],
        "Environment": ["pollution", "waste", "climate", "environment", "conservation", "recycling", "green", "sustainability", "ecology", "natural resources"],
    }
    return default_keywords.get(name, ["general", "service", "public", "government", "administration", "policy", "management", "official", "department", "civic"])

def analyze_petition(text, title, language='en'):
    api_key = app.config['OPENROUTER_API_KEY']
    if not api_key:
        from utils import analyze_petition_fallback
        return analyze_petition_fallback(text, title)
    
    try:
        # Translate if needed
        if language == 'ta':
            try:
                from googletrans import Translator
                translator = Translator()
                translated_title = translator.translate(title, dest='en').text
                translated_text = translator.translate(text[:1000], dest='en').text
            except Exception as e:
                logger.error(f"Translation error: {e}")
                translated_title, translated_text = title, text[:1000]
        else:
            translated_title, translated_text = title, text[:1000]
        
        # Get departments
        departments_data = list(db.departments.find({"name": {"$ne": ""}}))
        departments = [dept["name"] for dept in departments_data] or [
            "Education", "Health", "Infrastructure", "Environment", 
            "Public Safety", "Housing", "Social Welfare", "Transportation"
        ]
        
        # Use AI to analyze petition
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "qwen/qwen3-vl-30b-a3b-thinking",
                "messages": [
                    {"role": "system", "content": """You are an expert petition analyst for a government system in Tamil Nadu. 
                    Your tasks include analyzing petitions to determine the most relevant department, priority, and tags. 
                    Additionally, provide an estimated cost (in INR) and time (in days) for resolving the issue.
                    
                    Cost Estimation:
                    - Low priority: ₹1,000 - ₹5,000
                    - Normal priority: ₹5,000 - ₹20,000
                    - High priority: ₹20,000 - ₹50,000
                    
                    Time Estimation:
                    - Low priority: 7-30 days
                    - Normal priority: 3-15 days
                    - High priority: 1-7 days

                    When analyzing petitions:
                    1. Detect irrelevant content, spam, or random text and classify as "Low" priority under "General Administration" department.
                    2. Look for urgent language patterns in both Tamil and English.
                    3. Classify based on socioeconomic impact and time sensitivity.
                    4. Provide cost and time estimates based on priority."""},
                    {"role": "user", "content": f"""
                    Analyze this petition and provide a JSON response with:
                    1. department_name: Choose ONE department from this list that is MOST relevant:
                       {', '.join(departments)}
                    
                    2. priority: Classify STRICTLY as:
                       - "High" for urgent matters requiring immediate attention.
                       - "Normal" for standard requests that should be addressed but aren't immediately critical.
                       - "Low" for minor issues, suggestions, or irrelevant/spam messages.
                    
                    3. tags: Up to 5 relevant keywords as an array of strings.
                    4. analysis: Brief 2-3 sentence summary.
                    5. cost_estimate: Estimated cost in INR (use ranges based on priority).
                    6. time_estimate: Estimated time in days (use ranges based on priority).
                    
                    Petition Title: {translated_title}
                    Petition Content: {translated_text}
                    """}
                ],
                "response_format": {"type": "json_object"}
            },
            timeout=15
        )
        
        if response.status_code == 200:
            result_content = response.json()["choices"][0]["message"]["content"].strip()
            # Clean markdown formatting
            for prefix in ["```json", "```"]:
                if result_content.startswith(prefix):
                    result_content = result_content[len(prefix):]
            if result_content.endswith("```"):
                result_content = result_content[:-3]
            
            result = json.loads(result_content.strip())
            
            # Normalize priority
            priority_map = {"low": "Low", "normal": "Normal", "medium": "Normal", "high": "High", "urgent": "High"}
            result["priority"] = priority_map.get(str(result.get("priority", "")).lower(), "Normal")
            
            # Add cost and time estimation
            cost_estimates = {
                "Low": "₹1,000 - ₹5,000",
                "Normal": "₹5,000 - ₹20,000",
                "High": "₹20,000 - ₹50,000"
            }
            time_estimates = {
                "Low": "7-30 days",
                "Normal": "3-15 days",
                "High": "1-7 days"
            }
            result["cost_estimate"] = cost_estimates.get(result["priority"], "₹5,000 - ₹20,000")
            result["time_estimate"] = time_estimates.get(result["priority"], "3-15 days")
            
            # Ensure analysis exists
            if not result.get("analysis"):
                result["analysis"] = f"Petition related to {result['department_name']} department requiring review."
                
            # Generate Tamil translation of the analysis if the original was in Tamil
            if language == 'ta':
                try:
                    from googletrans import Translator
                    translator = Translator()
                    tamil_analysis = translator.translate(result["analysis"], src='en', dest='ta').text
                    result["tamil_analysis"] = tamil_analysis
                except Exception as e:
                    logger.error(f"Tamil translation error: {e}")
                    result["tamil_analysis"] = result["analysis"]
            
            return result
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
    
    # Fallback to rule-based analysis
    from utils import analyze_petition_fallback
    return analyze_petition_fallback(text, title)

def get_priority_color(priority):
    colors = {"High": "#d9534f", "Normal": "#f0ad4e", "Low": "#5cb85c"}
    return colors.get(priority, "#5bc0de")

def send_reminder_email(petition, official_email):
    try:
        petition_id = petition['_id']
        subject = f"REMINDER: Action Required - Petition #{petition_id} pending for over 3 days"
        status = db.petition_statuses.find_one({"_id": petition['status_id']})
        status_name = status['name'] if status else "Unknown"
        
        # Ensure proper timezone handling
        upload_time = petition['upload_time']
        if upload_time.tzinfo is None:
            upload_time = ist_tz.localize(upload_time)
        
        days_pending = (get_current_ist() - upload_time).days
        petition_url = url_for('view_petition', petition_id=str(petition_id), _external=True)
        
        # Email body
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px;">
                <h2 style="color: #d9534f;">Pending Petition Reminder</h2>
                <p>Dear {petition.get('department_name', '')} Department Official,</p>
                
                <p>This petition has been pending for <strong>{days_pending} days</strong> since submission.</p>
                
                <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #5bc0de; margin: 15px 0;">
                    <h3 style="margin-top: 0;">{petition.get('title', 'Untitled Petition')}</h3>
                    <p><strong>ID:</strong> {petition_id}</p>
                    <p><strong>Priority:</strong> <span style="color: {get_priority_color(petition.get('priority', 'Normal'))};">{petition.get('priority', 'Normal')}</span></p>
                    <p><strong>Status:</strong> {status_name}</p>
                    <p><strong>Submitted:</strong> {format_ist_date(upload_time)}</p>
                    <p><strong>Department:</strong> {petition.get('department_name', 'Unassigned')}</p>
                </div>
                
                <p><a href="{petition_url}" style="display: inline-block; padding: 10px 15px; background-color: #428bca; color: white; text-decoration: none; border-radius: 3px;">View Petition</a></p>
                
                <p>Please review this petition at your earliest convenience.</p>
                
                <p>Regards,<br>Petition Management System</p>
            </div>
        </body>
        </html>
        """
        
        msg = Message(subject=subject, recipients=[official_email], html=body)
        mail.send(msg)
        logger.info(f"Sent reminder email to {official_email} for petition {petition_id}")
        return True
    except Exception as e:
        logger.error(f"Email error: {str(e)}")
        return False

def save_file_to_mongodb(file_path, filename, content_type):
    try:
        with open(file_path, 'rb') as f:
            file_doc = {
                "filename": filename,
                "content_type": content_type,
                "data": f.read(),
                "upload_date": get_current_ist()
            }
        return str(db.files.insert_one(file_doc).inserted_id)
    except Exception as e:
        logger.error(f"Error saving file to MongoDB: {e}")
        return None

def get_file_from_mongodb(file_id):
    try:
        return db.files.find_one({"_id": ObjectId(file_id)})
    except Exception as e:
        logger.error(f"Error retrieving file from MongoDB: {e}")
        return None

def get_chart_data():
    try:
        # Status distribution
        status_data = [{
            "name": status["name"],
            "count": db.petitions.count_documents({"status_id": status["_id"]})
        } for status in db.petition_statuses.find()]
        
        # Priority distribution
        priority_data = [{
            "name": priority,
            "count": db.petitions.count_documents({"priority": priority})
        } for priority in ["High", "Normal", "Low"]]
        
        # Monthly petition counts
        monthly_data = []
        now = get_current_ist()
        for i in range(6):
            month_start = now.replace(day=1) - timedelta(days=30*i)
            month_end = (month_start + timedelta(days=31)).replace(day=1)
            count = db.petitions.count_documents({
                "upload_time": {"$gte": month_start, "$lt": month_end}
            })
            monthly_data.append({
                "month": month_start.strftime("%b %Y"),
                "count": count
            })
        
        return {
            "status_data": status_data,
            "priority_data": priority_data,
            "monthly_data": monthly_data[::-1]  # Reverse
        }
    except Exception as e:
        logger.error(f"Error getting chart data: {e}")
        return {"status_data": [], "priority_data": [], "monthly_data": []}

def automate_reminders():
    try:
        logger.info("Running automated reminders task")
        now = get_current_ist()
        three_days_ago = now - timedelta(days=3)
        
        # Find petitions pending for more than 3 days
        query = {
            "status_id": 1,  # Pending status
            "upload_time": {"$lt": three_days_ago},
            "$or": [
                {"last_reminder": {"$lt": now - timedelta(days=3)}},
                {"last_reminder": {"$exists": False}}
            ]
        }
        
        pending_petitions = list(db.petitions.find(query))
        logger.info(f"Found {len(pending_petitions)} petitions pending for more than 3 days")
        
        sent_count = 0
        for petition in pending_petitions:
            # Make sure upload_time is timezone-aware
            if 'upload_time' in petition and petition['upload_time'].tzinfo is None:
                petition['upload_time'] = ist_tz.localize(petition['upload_time'])
            
            department_name = petition.get('department_name')
            if not department_name:
                logger.warning(f"Petition {petition['_id']} has no department assigned")
                continue
            
            officials = list(db.users.find({"department": department_name, "role": "official"}))
            
            if not officials:
                # Notify admins if no officials found
                admins = list(db.users.find({"role": "admin"}))
                admin_emails = [admin['email'] for admin in admins]
                
                if admin_emails:
                    # Send reminder to admins instead
                    for admin_email in admin_emails:
                        if send_reminder_email(petition, admin_email):
                            sent_count += 1
                    
                    # Notify admins about missing officials
                    for admin in admins:
                        db.notifications.insert_one({
                            "user_id": admin['_id'],
                            "message": f"ATTENTION: No officials found for {department_name} department. Petition requires official assignment.",
                            "petition_id": petition["_id"],
                            "timestamp": now,
                            "is_read": False
                        })
                    
                    # Update last reminder timestamp
                    db.petitions.update_one(
                        {"_id": petition["_id"]},
                        {"$set": {"last_reminder": now}}
                    )
            else:
                # Send reminders to department officials
                for official in officials:
                    if send_reminder_email(petition, official['email']):
                        sent_count += 1
                
                if sent_count > 0:
                    db.petitions.update_one(
                        {"_id": petition["_id"]},
                        {"$set": {"last_reminder": now}}
                    )
        
        logger.info(f"Automated reminders: sent {sent_count} emails for {len(pending_petitions)} petitions")
        return {"success": True, "sent_count": sent_count, "petition_count": len(pending_petitions)}
    except Exception as e:
        logger.error(f"Email error: {str(e)}")
        return {"success": False, "error": str(e)}

def initialize_app_data():
    # Initialize petition statuses
    statuses = [
        (1, "Pending", "Petition received and awaiting initial review"),
        (2, "In Progress", "Officials are actively working on this petition"),
        (3, "Under Review", "Additional review or investigation needed"),
        (4, "Awaiting Response", "Waiting for additional information from petitioner"),
        (5, "Resolved", "Petition has been resolved"),
        (6, "Rejected", "Petition has been rejected")
    ]
    
    for id, name, desc in statuses:
        if not db.petition_statuses.find_one({"_id": id}):
            db.petition_statuses.insert_one({
                "_id": id, "name": name, "description": desc
            })
    
    # Create default admin user
    if not db.users.find_one({"email": "admin@petition-system.com"}):
        db.users.insert_one({
            "email": "admin@petition-system.com",
            "password": generate_password_hash("admin123"),
            "name": "Admin",
            "role": "admin",
            "verified": True
        })

# User model for Flask-Login
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.email = user_data['email']
        self.name = user_data.get('name', '')
        self.role = user_data.get('role', 'user')
        self.department = user_data.get('department', '')

# Template filters
@app.template_filter('nl2br')
def nl2br_filter(text):
    return Markup(text.replace('\n', '<br>')) if text else ""

@app.template_filter('format_date')
def format_date_filter(date):
    return format_ist_date(date)

@app.context_processor
def inject_now():
    return {'now': get_current_ist()}

# User loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    user_data = db.users.find_one({"_id": ObjectId(user_id)})
    return User(user_data) if user_data else None

# Routes
@app.route('/')
def home():
    """Landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        aadhar_no = request.form.get('aadhar_no')
        dob = request.form.get('dob')
        location = request.form.get('location')
        
        if not all([email, password, name, aadhar_no, dob, location]):
            flash("All fields are required", "danger")
            return render_template('register.html')
            
        if db.users.find_one({"email": email}):
            flash("Email already registered", "danger")
            return render_template('register.html')
        
        # Insert new user
        user_id = db.users.insert_one({
            "email": email,
            "password": generate_password_hash(password),
            "name": name,
            "role": "user",
            "registration_time": get_current_ist(),
            "verified": True
        }).inserted_id
        
        # Add verification data
        db.verifications.insert_one({
            "user_id": user_id,
            "name": name,
            "aadhar_no": aadhar_no,
            "dob": dob,
            "location": location,
            "verified_at": get_current_ist()
        })
        
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('login'))
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template('login.html')

        user_data = db.users.find_one({"email": email})
        if not user_data:
            flash("Invalid email or password.", "danger")
            return render_template('login.html')
        
        try:
            if check_password_hash(user_data['password'], password):
                login_user(User(user_data))
                next_page = request.args.get('next')
                if not next_page or not next_page.startswith('/'):
                    next_page = url_for('dashboard')
                return redirect(next_page)
            else:
                flash("Invalid email or password.", "danger")
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            flash("Authentication error. Please try again.", "danger")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        if not email:
            flash("Email is required.", "danger")
            return render_template('forgot_password.html')
        
        user = db.users.find_one({'email': email})
        if not user:
            # For security, show same message whether user exists or not
            flash("If your email is registered, you will receive password reset instructions.", "info")
            return redirect(url_for('login'))
        
        # Generate token and send email
        token = serializer.dumps(email, salt='password-reset-salt')
        reset_url = url_for('reset_password', token=token, _external=True)
        
        html = f'''
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="border: 1px solid #ddd; border-radius: 5px; padding: 20px;">
                <h2 style="color: #3a86ff;">Password Reset Request</h2>
                <p>Click the button below to reset your password. This link will expire in 1 hour.</p>
                <p style="text-align: center; margin: 25px 0;">
                    <a href="{reset_url}" style="background: #3a86ff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">Reset Your Password</a>
                </p>
                <p>If you did not request a password reset, please ignore this email.</p>
                <p>Regards,<br>Petition Management System</p>
            </div>
        </body>
        </html>
        '''
        
        try:
            msg = Message(subject="Password Reset Request - Petition System", recipients=[email], html=html)
            mail.send(msg)
            logger.info(f"Password reset email sent to {email}")
            flash("If your email is registered, you will receive password reset instructions.", "info")
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Error sending password reset email: {e}")
            flash("There was an error sending the password reset email. Please try again later.", "danger")
    
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    # Verify token
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)  # Token expires after 1 hour
    except SignatureExpired:
        flash("The password reset link has expired.", "danger")
        return redirect(url_for('forgot_password'))
    except BadSignature:
        flash("The password reset link is invalid.", "danger")
        return redirect(url_for('login'))
    
    # Check if user exists
    user = db.users.find_one({'email': email})
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not password or not confirm_password:
            flash("Both password fields are required.", "danger")
            return render_template('reset_password.html', token=token)
        
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('reset_password.html', token=token)
        
        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "danger")
            return render_template('reset_password.html', token=token)
        
        # Update user's password
        db.users.update_one(
            {'email': email},
            {'$set': {'password': generate_password_hash(password)}}
        )
        
        flash("Your password has been reset successfully. You can now log in with your new password.", "success")
        return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)

@app.route("/dashboard")
@login_required
def dashboard():    
    petition_statuses = {s['_id']: s['name'] for s in db.petition_statuses.find()}
    
    # Filter by role
    if current_user.role == 'official':
        query = {"department_name": current_user.department}
    elif current_user.role == 'admin':
        query = {}
    else:
        query = {"user_id": ObjectId(current_user.id)}
    
    # Pagination
    page = int(request.args.get('page', 1))
    per_page = 10
    skip = (page - 1) * per_page
    
    # Get petitions
    petitions = list(db.petitions.find(query).sort("upload_time", -1).skip(skip).limit(per_page))
    total = db.petitions.count_documents(query)
    total_pages = (total + per_page - 1) // per_page
    
    # Stats
    stats = {
        'total_petitions': db.petitions.count_documents({}),
        'pending_count': db.petitions.count_documents({"status_id": 1}),
        'in_progress_count': db.petitions.count_documents({"status_id": 2}),
        'resolved_count': db.petitions.count_documents({"status_id": 5}),
    }
    
    # Charts
    chart_data = get_chart_data()
    
    return render_template('dashboard.html',
                          petitions=petitions,
                          petition_statuses=petition_statuses,
                          stats=stats,
                          page=page,
                          total_pages=total_pages,
                          chart_data=chart_data)

@app.route("/upload_petition", methods=["GET", "POST"])
@login_required
def upload_petition():
    if request.method == "POST":
        # Get form data
        title = request.form.get("title", "").strip()
        content_text = request.form.get("content_text", "").strip()
        is_public = request.form.get("is_public") == "on"
        file = request.files.get('file')

        # Validate
        errors = []
        if not title or len(title) < 5:
            errors.append("Title must be at least 5 characters long")
            
        has_file = file and file.filename
        if not content_text and not has_file:
            errors.append("Either petition content or a file is required")
        
        allowed_extensions = {'pdf', 'txt', 'jpg', 'jpeg', 'png'}
        if has_file:
            filename = secure_filename(file.filename)
            if not filename or '.' not in filename or filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                errors.append("File type not allowed. Use PDF, TXT, JPG, or PNG")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("petition_form.html", form_data={
                "title": title, "content_text": content_text, "is_public": is_public
            })

        # Process file if provided
        file_name = None
        file_id = None
        
        if has_file:
            filename = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            file_name = unique_filename
            
            # Save to MongoDB
            content_type = file.content_type if hasattr(file, 'content_type') else 'application/octet-stream'
            file_id = save_file_to_mongodb(file_path, unique_filename, content_type)
            
            # Extract text if needed
            if not content_text:
                content_text = extract_text_from_file(file_path)

        # Analyze petition
        language = detect_language(content_text)
        analysis = analyze_petition(content_text, title, language)
        department_id = get_department_id(analysis["department_name"])

        # Create petition
        petition_id = db.petitions.insert_one({
    "title": title,
    "content_text": content_text,
    "file_name": file_name,
    "file_id": file_id,
    "is_public": is_public,
    "language": language,
    "priority": analysis["priority"],
    "department_id": department_id,
    "department_name": analysis["department_name"],
    "status_id": 1,  # Pending
    "upload_time": get_current_ist(),
    "user_id": ObjectId(current_user.id),
    "user_name": current_user.name,
    "tags": analysis.get("tags", []),
    "analysis": analysis.get("analysis", ""),
    "last_reminder": None,
    "cost_estimate": analysis.get("cost_estimate", ""),  
    "time_estimate": analysis.get("time_estimate", "")   
}).inserted_id

        # Notify officials
        officials = list(db.users.find({"role": "official", "department": analysis["department_name"]}))
        for official in officials:
            db.notifications.insert_one({
                "user_id": official["_id"],
                "petition_id": petition_id,
                "message": f"New {analysis['priority']} priority petition: {title}",
                "timestamp": get_current_ist(),
                "is_read": False
            })

        flash(f"Petition submitted successfully as {analysis['priority']} priority for {analysis['department_name']} department.", "success")
        return redirect(url_for('view_petition', petition_id=petition_id))

    return render_template("petition_form.html", form_data={})

@app.route("/petition/<petition_id>")
@login_required
def view_petition(petition_id):
    try:
        petition = db.petitions.find_one({"_id": ObjectId(petition_id)})
        
        if not petition:
            flash("Petition not found", "danger")
            return redirect(url_for('dashboard'))
        
        # Check permissions
        is_owner = str(petition.get('user_id', '')) == current_user.id
        is_public = petition.get('is_public', False)
        
        if not is_owner and not is_public and current_user.role == 'user':
            flash("You don't have permission to view this petition", "danger")
            return redirect(url_for('dashboard'))
        
        if current_user.role == 'official' and petition.get('department_name') != current_user.department:
            flash("This petition is not assigned to your department", "danger")
            return redirect(url_for('dashboard'))
        
        # Get related data
        status = db.petition_statuses.find_one({"_id": petition['status_id']})
        department = db.departments.find_one({"_id": petition['department_id']}) if 'department_id' in petition else None
        comments = list(db.comments.find({"petition_id": ObjectId(petition_id)}).sort("timestamp", 1))
        updates = list(db.status_updates.find({"petition_id": ObjectId(petition_id)}).sort("timestamp", -1))
        
        # Get petition statuses for officials/admin
        petition_statuses = {s['_id']: s['name'] for s in db.petition_statuses.find()} if current_user.role in ['official', 'admin'] else {}
        
        # Get similar petitions
        similar_petitions = []
        if petition.get('tags'):
            similar_query = {
                "_id": {"$ne": ObjectId(petition_id)},
                "tags": {"$in": petition['tags']},
            }
            
            # Apply permission filtering
            if current_user.role == 'user':
                similar_query["$or"] = [{"is_public": True}, {"user_id": ObjectId(current_user.id)}]
            elif current_user.role == 'official':
                similar_query["department_name"] = current_user.department
                    
            similar_petitions = list(db.petitions.find(similar_query).limit(3))
        
        # Get departments for admin
        departments = list(db.departments.find().sort("name", 1)) if current_user.role == 'admin' else []
        
        # Get verification data
        verification = db.verifications.find_one({"user_id": petition.get('user_id')}) if petition.get('user_id') else None
        
        return render_template('petition_detail.html',
                              petition=petition,
                              status=status,
                              department=department,
                              comments=comments,
                              updates=updates,
                              petition_statuses=petition_statuses,
                              similar_petitions=similar_petitions,
                              departments=departments,
                              verification=verification)
        
    except Exception as e:
        logger.error(f"Error viewing petition: {str(e)}")
        flash(f"Error viewing petition. Please try again.", "danger")
        return redirect(url_for('dashboard'))

@app.route("/add_comment/<petition_id>", methods=["POST"])
@login_required
def add_comment(petition_id):
    try:
        comment_text = request.form.get("comment_text")
        if not comment_text:
            flash("Comment cannot be empty.", "danger")
            return redirect(url_for("view_petition", petition_id=petition_id))
        
        petition_obj_id = ObjectId(petition_id)
        petition = db.petitions.find_one({"_id": petition_obj_id})
        
        if not petition:
            flash("Petition not found.", "danger")
            return redirect(url_for("dashboard"))
        
        # Check permission
        is_owner = str(petition.get('user_id', '')) == current_user.id
        is_public = petition.get('is_public', False)
        can_comment = is_owner or is_public or current_user.role in ['official', 'admin']
        
        if not can_comment:
            flash("You don't have permission to comment on this petition.", "danger")
            return redirect(url_for("dashboard"))
        
        # Add comment
        db.comments.insert_one({
            "petition_id": petition_obj_id,
            "user_id": ObjectId(current_user.id),
            "user_name": current_user.name,
            "text": comment_text,
            "timestamp": get_current_ist(),
            "is_system": False
        })
        
        # Notify petition owner if comment is from official or admin
        if current_user.role in ['official', 'admin'] and str(petition.get("user_id", '')) != current_user.id:
            db.notifications.insert_one({
                "user_id": petition["user_id"],
                "petition_id": petition_obj_id,
                "message": f"New official comment on your petition: {petition['title']}",
                "timestamp": get_current_ist(),
                "is_read": False
            })
        
        # Notify officials if comment is from petition owner
        if is_owner and current_user.role == 'user':
            officials = list(db.users.find({"role": "official", "department": petition.get("department_name")}))
            for official in officials:
                db.notifications.insert_one({
                    "user_id": official["_id"],
                    "petition_id": petition_obj_id,
                    "message": f"New comment from petitioner on: {petition['title']}",
                    "timestamp": get_current_ist(),
                    "is_read": False
                })
            
        flash("Comment added successfully.", "success")
        return redirect(url_for("view_petition", petition_id=petition_id))
    except Exception as e:
        logger.error(f"Error adding comment: {e}")
        flash("An error occurred.", "danger")
        return redirect(url_for("view_petition", petition_id=petition_id))

@app.route("/update_status/<petition_id>", methods=["POST"])
@login_required
def update_status(petition_id):
    try:
        # Check permissions
        if current_user.role not in ['official', 'admin']:
            flash("Permission denied.", "danger")
            return redirect(url_for("view_petition", petition_id=petition_id))
        
        petition_obj_id = ObjectId(petition_id)
        petition = db.petitions.find_one({"_id": petition_obj_id})
        
        if not petition:
            flash("Petition not found.", "danger")
            return redirect(url_for("dashboard"))
        
        # Check department permission for officials
        if current_user.role == 'official' and petition.get("department_name"):
            if current_user.department != petition.get("department_name"):
                flash("You can only update petitions for your department.", "danger")
                return redirect(url_for("view_petition", petition_id=petition_id))
        
        # Get form data
        new_status_id = int(request.form.get("status_id"))
        notes = request.form.get("notes", "")
        old_status_id = petition["status_id"]
        
        # Update petition
        update_data = {"status_id": new_status_id}
        if new_status_id == 5:  # Resolved
            update_data.update({
                "resolution_time": get_current_ist(),
                "resolution_notes": notes
            })
        
        db.petitions.update_one({"_id": petition_obj_id}, {"$set": update_data})
        
        # Get status names
        old_status = db.petition_statuses.find_one({"_id": old_status_id})
        new_status = db.petition_statuses.find_one({"_id": new_status_id})
        old_status_name = old_status["name"] if old_status else "Unknown"
        new_status_name = new_status["name"] if new_status else "Unknown"
        
        # Record status update
        db.status_updates.insert_one({
            "petition_id": petition_obj_id,
            "old_status_id": old_status_id,
            "new_status_id": new_status_id,
            "old_status_name": old_status_name,
            "new_status_name": new_status_name,
            "notes": notes,
            "updated_by": ObjectId(current_user.id),
            "updated_by_name": current_user.name,
            "timestamp": get_current_ist()
        })
        
        # Add system comment
        db.comments.insert_one({
            "petition_id": petition_obj_id,
            "user_id": ObjectId(current_user.id),
            "user_name": "System",
            "text": f"Status changed from {old_status_name} to {new_status_name} by {current_user.name}.\n{notes}",
            "timestamp": get_current_ist(),
            "is_system": True
        })
        
        # Notify the petition owner
        db.notifications.insert_one({
            "user_id": petition["user_id"],
            "petition_id": petition_obj_id,
            "message": f"Your petition '{petition['title']}' is now {new_status_name}",
            "timestamp": get_current_ist(),
            "is_read": False
        })
        
        flash(f"Status updated to {new_status_name}.", "success")
        return redirect(url_for("view_petition", petition_id=petition_id))
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        flash(f"Error updating status.", "danger")
        return redirect(url_for("view_petition", petition_id=petition_id))

@app.route("/delete_petition/<petition_id>", methods=["POST"])
@login_required
def delete_petition(petition_id):
    try:
        petition_obj_id = ObjectId(petition_id)
        petition = db.petitions.find_one({"_id": petition_obj_id})
        
        if not petition:
            flash("Petition not found.", "danger")
            return redirect(url_for("dashboard"))
        
        is_owner = str(petition.get('user_id', '')) == current_user.id
        is_admin = current_user.role == 'admin'
        
        if not (is_owner or is_admin):
            flash("You don't have permission to delete this petition.", "danger")
            return redirect(url_for("view_petition", petition_id=petition_id))
        
        # Delete related data
        db.comments.delete_many({"petition_id": petition_obj_id})
        db.status_updates.delete_many({"petition_id": petition_obj_id})
        db.notifications.delete_many({"petition_id": petition_obj_id})
        
        # Delete file if exists
        if petition.get('file_id'):
            db.files.delete_one({"_id": ObjectId(petition.get('file_id'))})
            
        if petition.get('file_name'):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], petition['file_name'])
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # Delete petition
        db.petitions.delete_one({"_id": petition_obj_id})
        
        flash("Petition deleted successfully.", "success")
        return redirect(url_for("dashboard"))
    except Exception as e:
        logger.error(f"Error deleting petition: {e}")
        flash("An error occurred while deleting the petition.", "danger")
        return redirect(url_for("view_petition", petition_id=petition_id))

@app.route("/admin/manage", methods=["GET", "POST"])
@login_required
@requires_roles('admin')
def admin_manage():
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "add_department":
            name = request.form.get("name")
            description = request.form.get("description", "")
            
            if name and not db.departments.find_one({"name": name}):
                # Generate keywords and insert department
                keywords = generate_department_keywords(name)
                db.departments.insert_one({
                    "name": name,
                    "description": description,
                    "keywords": keywords
                })
                flash(f"Department '{name}' added successfully with {len(keywords)} keywords.", "success")
            else:
                flash("Department name required or already exists.", "danger")
        
        elif action == "add_official":
            email = request.form.get("email")
            name = request.form.get("name")
            password = request.form.get("password")
            department = request.form.get("department")
            
            if email and name and password and department:
                if not db.users.find_one({"email": email}):
                    db.users.insert_one({
                        "email": email,
                        "name": name,
                        "password": generate_password_hash(password),
                        "role": "official",
                        "department": department,
                        "verified": True
                    })
                    flash(f"Official '{name}' added to {department} department.", "success")
                else:
                    flash("Email already exists.", "danger")
            else:
                flash("All fields are required.", "danger")
    
    # Get data for the template
    departments = list(db.departments.find())
    officials = list(db.users.find({"role": "official"}))
    users = list(db.users.find({"role": "user"}).limit(100))
    
    # Department analytics
    department_counts = [{
        "name": dept["name"],
        "count": db.petitions.count_documents({"department_name": dept["name"]})
    } for dept in departments]
    department_counts.sort(key=lambda x: x["count"], reverse=True)
    
    # Chart data
    chart_data = get_chart_data()
    
    # Scheduler next run time
    next_run_time = datetime.now(ist_tz) + timedelta(
        hours=(24 - datetime.now(ist_tz).hour),
        minutes=-datetime.now(ist_tz).minute,
        seconds=-datetime.now(ist_tz).second
    )
    
    return render_template(
        "admin_manage.html",
        departments=departments,
        officials=officials,
        users=users,
        department_counts=department_counts,
        chart_data=chart_data,
        next_run_time=next_run_time
    )

@app.route('/send_reminders')
@login_required
@requires_roles('admin')
def send_reminders():
    try:
        result = automate_reminders()
        
        if result.get("success"):
            flash(f"Reminders sent successfully. Sent {result.get('sent_count', 0)} emails for {result.get('petition_count', 0)} petitions.", "success")
        else:
            flash(f"Error sending reminders: {result.get('error', 'Unknown error')}", "danger")
            
        return redirect(url_for("admin_manage"))
    except Exception as e:
        logger.error(f"Error in manual reminders: {e}")
        flash(f"Error sending reminders: {str(e)}", "danger")
        return redirect(url_for("admin_manage"))

@app.route("/reassign_department/<petition_id>", methods=["POST"])
@login_required
@requires_roles('admin')
def reassign_department(petition_id):
    try:
        petition_obj_id = ObjectId(petition_id)
        petition = db.petitions.find_one({"_id": petition_obj_id})
        
        if not petition:
            flash("Petition not found", "danger")
            return redirect(url_for('dashboard'))
            
        new_department = request.form.get("department")
        notes = request.form.get("reassign_notes", "")
        
        if not new_department:
            flash("Department is required", "danger")
            return redirect(url_for('view_petition', petition_id=petition_id))
            
        old_department = petition.get("department_name")
        if old_department == new_department:
            flash("Petition is already assigned to this department", "info")
            return redirect(url_for('view_petition', petition_id=petition_id))
            
        department = db.departments.find_one({"name": new_department})
        if not department:
            flash("Invalid department", "danger")
            return redirect(url_for('view_petition', petition_id=petition_id))
            
        # Update petition
        db.petitions.update_one(
            {"_id": petition_obj_id},
            {"$set": {
                "department_id": department["_id"],
                "department_name": new_department
            }}
        )
        
        # Add system comment
        db.comments.insert_one({
            "petition_id": petition_obj_id,
            "user_id": ObjectId(current_user.id),
            "user_name": "System",
            "text": f"Petition reassigned from {old_department} to {new_department} department by {current_user.name}.\n{notes}",
            "timestamp": get_current_ist(),
            "is_system": True
        })
        
        # Notify petition owner
        db.notifications.insert_one({
            "user_id": petition["user_id"],
            "petition_id": petition_obj_id,
            "message": f"Your petition '{petition['title']}' has been reassigned to {new_department} Department",
            "timestamp": get_current_ist(),
            "is_read": False
        })
        
        # Notify officials in the new department
        officials = list(db.users.find({"role": "official", "department": new_department}))
        for official in officials:
            db.notifications.insert_one({
                "user_id": official["_id"],
                "petition_id": petition_obj_id,
                "message": f"New petition assigned to your department: {petition['title']}",
                "timestamp": get_current_ist(),
                "is_read": False
            })
            
        flash(f"Petition reassigned to {new_department} Department", "success")
        return redirect(url_for('view_petition', petition_id=petition_id))
    except Exception as e:
        logger.error(f"Error reassigning petition: {str(e)}")
        flash("Error reassigning petition", "danger")
        return redirect(url_for('view_petition', petition_id=petition_id))

@app.route("/notifications")
@login_required
def view_notifications():
    notifications = list(db.notifications.find(
        {"user_id": ObjectId(current_user.id)}
    ).sort("timestamp", -1).limit(20))
    
    # Mark all as read
    db.notifications.update_many(
        {"user_id": ObjectId(current_user.id), "is_read": False},
        {"$set": {"is_read": True}}
    )
    
    return render_template("notifications.html", notifications=notifications)

@app.route('/api/notifications/count')
@login_required
def get_notification_count():
    try:
        unread_count = db.notifications.count_documents({
            "user_id": ObjectId(current_user.id),
            "is_read": False
        })
        return jsonify({"count": unread_count})
    except Exception as e:
        logger.error(f"Error getting notification count: {e}")
        return jsonify({"count": 0, "error": str(e)})

@app.route("/petitions")
@login_required
def view_petitions():
    # Get filter parameters
    status_filter = request.args.get('status')
    priority_filter = request.args.get('priority')
    department_filter = request.args.get('department')
    search_query = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = 12
    
    # Build query based on filters
    query = {}
    
    if status_filter:
        try:
            query["status_id"] = int(status_filter)
        except ValueError:
            pass
    
    if priority_filter:
        query["priority"] = priority_filter
    
    if department_filter:
        query["department_name"] = department_filter
    
    # Handle search
    if search_query:
        query["$or"] = [
            {"title": {"$regex": search_query, "$options": "i"}},
            {"content_text": {"$regex": search_query, "$options": "i"}},
            {"tags": {"$in": [search_query.lower()]}}
        ]
    
    # Apply permissions based on user role
    if current_user.role == 'user':
        query = {"$and": [query, {"$or": [{"user_id": ObjectId(current_user.id)}, {"is_public": True}]}]}
    elif current_user.role == 'official':
        query["department_name"] = current_user.department
    
    # Calculate pagination
    skip = (page - 1) * per_page
    total = db.petitions.count_documents(query)
    total_pages = (total + per_page - 1) // per_page
    
    # Fetch petitions with pagination
    petitions = list(db.petitions.find(query).sort("upload_time", -1).skip(skip).limit(per_page))
    
    # Get data for dropdowns
    petition_statuses = {s['_id']: s['name'] for s in db.petition_statuses.find()}
    departments = list(db.departments.find().sort("name", 1))
    statuses = list(db.petition_statuses.find())
    
    return render_template("petitions.html",
                          petitions=petitions,
                          petition_statuses=petition_statuses,
                          departments=departments,
                          statuses=statuses,
                          total_pages=total_pages,
                          page=page,
                          status_filter=status_filter,
                          priority_filter=priority_filter,
                          department_filter=department_filter,
                          search_query=search_query)

@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(file_path):
            flash("File not found", "danger")
            return redirect(url_for('dashboard'))
            
        # Find associated petition
        petition = db.petitions.find_one({"file_name": filename})
        
        # Check permissions
        if petition:
            is_owner = str(petition.get('user_id', '')) == current_user.id
            is_public = petition.get('is_public', False)
            can_access = is_owner or is_public or current_user.role in ['official', 'admin']
            
            if current_user.role == 'official' and petition.get('department_name') != current_user.department:
                can_access = False
                
            if not can_access:
                flash("You don't have permission to access this file", "danger")
                return redirect(url_for('dashboard'))
        
        return send_file(file_path, download_name=filename, as_attachment=False)
    except Exception as e:
        logger.error(f"Error accessing file: {str(e)}")
        flash("Error accessing file. Please try again.", "danger")
        return redirect(url_for('dashboard'))

@app.route("/file/<file_id>")
@login_required
def get_file(file_id):
    try:
        file_doc = get_file_from_mongodb(file_id)
        
        if not file_doc:
            flash("File not found", "danger")
            return redirect(url_for('dashboard'))
            
        # Find petition this file belongs to
        petition = db.petitions.find_one({"file_id": file_id})
        
        # Check permissions
        if petition:
            is_owner = str(petition.get('user_id', '')) == current_user.id
            is_public = petition.get('is_public', False)
            can_access = is_owner or is_public or current_user.role in ['official', 'admin']
            
            if current_user.role == 'official' and petition.get('department_name') != current_user.department:
                can_access = False
                
            if not can_access:
                flash("You don't have permission to access this file", "danger")
                return redirect(url_for('dashboard'))
        
        response = make_response(file_doc['data'])
        response.headers['Content-Type'] = file_doc.get('content_type', 'application/octet-stream')
        response.headers['Content-Disposition'] = f'inline; filename="{file_doc["filename"]}"'
        
        return response
    except Exception as e:
        logger.error(f"Error accessing file: {str(e)}")
        flash("Error accessing file. Please try again.", "danger")
        return redirect(url_for('dashboard'))

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', message="The page you're looking for was not found."), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return render_template('error.html', message="An internal server error occurred."), 500

# Scheduler for automated reminders
scheduler = BackgroundScheduler()
scheduler.add_job(
    automate_reminders, 
    'cron', 
    hour=0,  # Midnight IST
    minute=0, 
    id='daily_reminders'
)

if __name__ == '__main__':
    initialize_app_data()
    scheduler.start()
    try:
        app.run(debug=False, host='0.0.0.0')
    except (KeyboardInterrupt, SystemExit):

        scheduler.shutdown()

