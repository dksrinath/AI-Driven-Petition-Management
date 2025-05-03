import re
import logging
from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def analyze_petition_fallback(text, title):
    """Enhanced rule-based petition analysis when AI service unavailable."""
    # Connect to database
    load_dotenv()
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/petition')
    client = MongoClient(mongo_uri)
    db = client.get_database()
    
    # Build department keywords from database
    department_keywords = {}
    for dept in db.departments.find():
        dept_name = dept.get("name")
        keywords = dept.get("keywords", [])
        if dept_name and keywords:
            department_keywords[dept_name] = keywords
    
    # Fallback keywords if database has no data - Enhanced with Tamil terms
    if not department_keywords:
        department_keywords = {
            "Education": ["school", "education", "student", "teacher", "curriculum", "university", "college", "பள்ளி", "கல்வி", "மாணவர்", "ஆசிரியர்", "பல்கலைக்கழகம்"],
            "Health": ["health", "hospital", "doctor", "medical", "patient", "clinic", "disease", "மருத்துவமனை", "சுகாதாரம்", "மருத்துவர்", "நோய்", "நோயாளி"],
            "Infrastructure": ["road", "bridge", "construction", "building", "highway", "electricity", "water", "சாலை", "பாலம்", "கட்டுமானம்", "மின்சாரம்", "தண்ணீர்"],
            "Environment": ["pollution", "waste", "climate", "environment", "forest", "tree", "water", "மாசு", "சுற்றுச்சூழல்", "காடு", "மரம்", "கழிவு"],
            "Public Safety": ["police", "crime", "safety", "emergency", "security", "fire", "accident", "காவல்", "பாதுகாப்பு", "அவசரம்", "விபத்து", "தீ"],
            "Housing": ["housing", "home", "rent", "shelter", "apartment", "property", "வீடு", "குடியிருப்பு", "வாடகை", "சொத்து"],
            "Social Welfare": ["welfare", "benefit", "aid", "support", "pension", "ration", "scholarship", "நலன்", "உதவி", "ஓய்வூதியம்", "ரேஷன்", "உதவித்தொகை"],
            "Transportation": ["transport", "bus", "train", "traffic", "vehicle", "metro", "airport", "போக்குவரத்து", "பேருந்து", "ரயில்", "வாகனம்"],
            "Agriculture": ["farming", "crops", "irrigation", "farmers", "agriculture", "seed", "விவசாயம்", "பயிர்", "விவசாயி", "நீர்ப்பாசனம்", "விதை"],
            "General Administration": ["general", "administration", "complaint", "petition", "official", "government", "application", "நிர்வாகம்", "புகார்", "மனு", "அரசு", "விண்ணப்பம்"]
        }
    
    # Define urgency keywords with enhanced Tamil support
    urgent_keywords = [
        "urgent", "immediately", "emergency", "critical", "deadline", "asap", 
        "அவசரம்", "உடனடி", "அவசியம்", "அத்தியாவசிய", "கடைசி நாள்", "விரைவாக"
    ]
    
    # Normalize and prepare text for analysis
    combined_text = f"{title} {text}".lower()
    
    # Count keyword matches for each department
    department_scores = {}
    for department, keywords in department_keywords.items():
        score = 0
        for keyword in keywords:
            if keyword.lower() in combined_text:
                score += 1
        department_scores[department] = score
    
    # Determine department with highest match
    if department_scores:
        department = max(department_scores.items(), key=lambda x: x[1])[0]
    else:
        department = "General Administration"
    
    # Determine urgency score based on keywords and text length
    urgent_score = 0
    for keyword in urgent_keywords:
        if keyword.lower() in combined_text:
            urgent_score += 1
            
    # Check if text is random or too short (potential spam)
    is_random = len(text.strip()) < 15 or len(title.strip()) < 3
    
    # Determine priority based on urgency and content validity
    if is_random:
        priority = "Low"
    elif urgent_score >= 2:
        priority = "High"
    elif urgent_score == 1:
        priority = "Normal"
    else:
        priority = "Normal"
    
    # Generate tags
    tags = []
    if is_random:
        tags = ["review", "unclear", "needs verification"]
    else:
        # Extract potential keywords from text (simple implementation)
        words = combined_text.split()
        for dept, keywords in department_keywords.items():
            for keyword in keywords:
                if keyword.lower() in combined_text and keyword.lower() not in tags:
                    tags.append(keyword.lower())
                    if len(tags) >= 5:
                        break
            if len(tags) >= 5:
                break
    
    # Generate analysis
    if is_random:
        analysis = "This submission appears to be incomplete or contains random text. Further verification is needed."
    else:
        analysis = f"Petition concerning {department} department issues. "
        if urgent_score > 0:
            analysis += "Contains urgent language suggesting time sensitivity. "
        analysis += f"Classified with {len(tags)} relevant keywords."
    
    # Determine if the text contains Tamil
    tamil_chars = set("அஆஇஈஉஊஎஏஐஒஓஔகஙசஞடணதநபமயரலவழளறனஜஷஸஹ")
    has_tamil = len(set(combined_text) & tamil_chars) > 5
    
    # Generate Tamil analysis if needed
    tamil_analysis = ""
    if has_tamil:
        # Simple Tamil analysis template
        if is_random:
            tamil_analysis = "இந்த மனு முழுமையற்றதாக அல்லது ஏதேனும் சம்பந்தமில்லாத உரையைக் கொண்டுள்ளது. மேலும் சரிபார்ப்பு தேவை."
        else:
            tamil_analysis = f"{department} துறை தொடர்பான மனு. "
            if urgent_score > 0:
                tamil_analysis += "அவசர கவனம் தேவைப்படுகிறது. "
            tamil_analysis += f"{len(tags)} சிறப்புச் சொற்களுடன் வகைப்படுத்தப்பட்டுள்ளது."
    
    result = {
        "department_name": department,
        "priority": priority,
        "tags": tags[:5],  # Limit to 5 tags
        "analysis": analysis
    }
    
    if has_tamil:
        result["tamil_analysis"] = tamil_analysis
    
    return result