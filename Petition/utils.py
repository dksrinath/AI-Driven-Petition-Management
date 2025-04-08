import re
import logging
from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def analyze_petition_fallback(text, title):
    """Rule-based petition analysis when AI service unavailable."""
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
    
    # Fallback keywords if database has no data
    if not department_keywords:
        department_keywords = {
            "Education": ["school", "education", "student", "teacher", "curriculum", "university", "பள்ளி", "கல்வி"],
            "Health": ["health", "hospital", "doctor", "medical", "patient", "clinic", "மருத்துவமனை", "சுகாதாரம்"],
            "Infrastructure": ["road", "bridge", "construction", "building", "highway", "சாலை", "பாலம்", "கட்டுமானம்"],
            "Environment": ["pollution", "waste", "climate", "environment", "forest", "tree", "மாசு", "சுற்றுச்சூழல்"],
            "Public Safety": ["police", "crime", "safety", "emergency", "security", "fire", "காவல்", "பாதுகாப்பு"],
            "Housing": ["housing", "home", "rent", "shelter", "apartment", "property", "வீடு", "குடியிருப்பு"],
            "Social Welfare": ["welfare", "benefit", "aid", "support", "pension", "நலன்", "உதவி"],
            "Transportation": ["transport", "bus", "train", "traffic", "vehicle", "metro", "போக்குவரத்து", "பேருந்து"],
            "Agriculture": ["farming", "crops", "irrigation", "farmers", "விவசாயம்", "பயிர்", "விவசாயி"]
        }
    
    # Define urgency keywords
    urgent_keywords = ["urgent", "immediately", "emergency", "critical", "அவசரம்", "உடனடி"]
    low_priority_keywords = ["minor", "small", "suggestion", "consider", "சிறிய", "பரிந்துரை"]
    
    # Process text
    combined_text = (text + " " + title).lower()
    
    # Calculate department scores
    dept_scores = {}
    for dept, keywords in department_keywords.items():
        score = 0
        for keyword in keywords:
            if isinstance(keyword, str) and keyword.lower() in combined_text:
                # Higher weight for title matches
                if keyword.lower() in title.lower():
                    score += 3
                else:
                    score += 1
        dept_scores[dept] = score
    
    # Select highest scoring department
    max_score = max(dept_scores.values()) if dept_scores else 0
    if max_score > 0:
        # Handle ties by finding earliest keyword mention
        top_depts = [d for d, s in dept_scores.items() if s == max_score]
        if len(top_depts) == 1:
            department = top_depts[0]
        else:
            earliest_pos = {}
            for dept in top_depts:
                positions = [combined_text.find(kw.lower()) for kw in department_keywords[dept] 
                            if isinstance(kw, str) and combined_text.find(kw.lower()) >= 0]
                earliest_pos[dept] = min(positions) if positions else len(combined_text)
            department = min(earliest_pos.items(), key=lambda x: x[1])[0]
    else:
        # Default department if no matches
        default_dept = next((dept["name"] for dept in db.departments.find() if dept["name"] == "Social Welfare"), None)
        department = default_dept or (db.departments.find_one() or {}).get("name") or "Social Welfare"
    
    # Determine priority
    if any(word in combined_text for word in urgent_keywords):
        priority = "High"
    elif any(word in combined_text for word in low_priority_keywords):
        priority = "Low"
    else:
        priority = "Normal"
    
    # Extract tags
    clean_text = re.sub(r'[^\w\s]', ' ', combined_text)
    words = clean_text.split()
    
    # Remove common words
    common_words = ["the", "and", "a", "in", "of", "to", "for", "with", "that", "this", 
                   "i", "we", "you", "he", "she", "it", "they", "is", "are", "was", 
                   "please", "thank", "ஒரு", "மற்றும்", "அந்த", "இந்த"]
    
    filtered_words = [word for word in words if word.lower() not in common_words and len(word) > 3]
    
    # Count word frequency
    word_counts = {}
    for word in filtered_words:
        word_counts[word.lower()] = word_counts.get(word.lower(), 0) + 1
    
    # Get top 5 tags
    tags = [tag for tag, _ in sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
    
    # Generate analysis summary
    if priority == "High":
        analysis = f"This is a high-priority petition related to {department.lower()} issues. "
    else:
        analysis = f"This petition concerns {department.lower()} matters with {priority.lower()} priority. "
    
    if "request" in combined_text:
        analysis += "The petitioner is requesting specific action or intervention."
    elif "complaint" in combined_text or "issue" in combined_text:
        analysis += "The petitioner is reporting a problem that needs resolution."
    elif "suggest" in combined_text or "recommend" in combined_text:
        analysis += "The petitioner is making suggestions for improvement."
    else:
        analysis += "Requires attention from the appropriate department."
    
    return {
        "department_name": department,
        "priority": priority,
        "tags": tags,
        "analysis": analysis
    }