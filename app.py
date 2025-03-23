from flask import Flask, request, jsonify, render_template, send_from_directory, session
from datetime import datetime, date
import os
import io
import google.generativeai as genai
from PIL import Image
import json
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-secret-key')

# PostgreSQL Database Configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/meals_db')
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Models
class DailyNutrition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    calories = db.Column(db.Float, default=0)
    proteins = db.Column(db.Float, default=0)
    carbs = db.Column(db.Float, default=0)
    fats = db.Column(db.Float, default=0)
    streak = db.Column(db.Integer, default=0)

class Meal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    name = db.Column(db.String(200), nullable=False)
    calories = db.Column(db.Float)
    proteins = db.Column(db.Float)
    carbs = db.Column(db.Float)
    fats = db.Column(db.Float)
    time_added = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()

# Configure Gemini API
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)

# Initialize the Gemini model
generation_config = {
    "temperature": 0.4,
    "top_p": 1,
    "top_k": 32,
    "max_output_tokens": 1024,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    generation_config=generation_config,
    safety_settings=safety_settings
)

def get_or_create_daily_record():
    today = date.today()
    record = DailyNutrition.query.filter_by(date=today).first()
    if not record:
        record = DailyNutrition(date=today)
        db.session.add(record)
        db.session.commit()
    return record

@app.route('/toggle-theme')
def toggle_theme():
    current_theme = session.get('theme', 'light')
    session['theme'] = 'dark' if current_theme == 'light' else 'light'
    return jsonify({'theme': session['theme']})

@app.route('/')
def index():
    theme = session.get('theme', 'light')
    return render_template('index.html', theme=theme)

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image uploaded'})
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No image selected'})

        # Process image with PIL
        img = Image.open(file.stream)
        
        # Convert PIL Image to bytes for Gemini
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format=img.format or 'JPEG')
        img_byte_arr = img_byte_arr.getvalue()

        # Get additional context from form data
        food_name = request.form.get('food_name', '')
        food_quantity = request.form.get('food_quantity', '')
        
        # Construct the prompt
        prompt = f"""You are a professional nutritionist analyzing food images. Analyze the following food image:
Food name: {food_name}
{f'Quantity: {food_quantity}' if food_quantity else 'Look for size references in the image (coins, hands) to estimate portion size or estimate yourself if not found'}

Return ONLY a JSON object with the following fields (all numbers should be floats):
{{
    "calories": estimated_calories,
    "proteins": protein_grams,
    "carbs": carb_grams,
    "fats": fat_grams
}}"""
        
        response = model.generate_content(
            contents=[
                prompt,
                {"mime_type": "image/jpeg", "data": img_byte_arr}
            ],
            stream=False,
            generation_config={
                "temperature": 0.1,
                "top_p": 1,
                "top_k": 1,
                "max_output_tokens": 1024,
                "candidate_count": 1
            }
        )
        
        if response.prompt_feedback.block_reason:
            return jsonify({'success': False, 'error': 'Content blocked by safety settings'})
            
        response_text = response.text
        
        try:
            # Clean the response text to ensure valid JSON
            json_str = response_text.strip()
            if not (json_str.startswith('{') and json_str.endswith('}')):
                start_idx = json_str.find('{')
                end_idx = json_str.rfind('}') + 1
                if start_idx != -1 and end_idx > 0:
                    json_str = json_str[start_idx:end_idx]
                else:
                    raise ValueError("No valid JSON object found in response")
            
            nutrition_data = json.loads(json_str)
            
            # Validate the required fields
            required_fields = ['calories', 'proteins', 'carbs', 'fats']
            if not all(field in nutrition_data for field in required_fields):
                raise ValueError("Missing required nutritional fields")
                
            # Convert values to float
            for field in required_fields:
                nutrition_data[field] = float(nutrition_data.get(field, 0))
                
        except (json.JSONDecodeError, ValueError) as e:
            return jsonify({'success': False, 'error': f'Invalid response format: {str(e)}'})
        
        # Update daily totals in database
        daily_record = get_or_create_daily_record()
        daily_record.calories += nutrition_data['calories']
        daily_record.proteins += nutrition_data['proteins']
        daily_record.carbs += nutrition_data['carbs']
        daily_record.fats += nutrition_data['fats']
        
        # Create new meal record
        new_meal = Meal(
            date=date.today(),
            name=food_name,
            calories=nutrition_data['calories'],
            proteins=nutrition_data['proteins'],
            carbs=nutrition_data['carbs'],
            fats=nutrition_data['fats']
        )
        db.session.add(new_meal)
        db.session.commit()
            
        # Get updated daily totals
        daily_total = {
            'calories': daily_record.calories,
            'proteins': daily_record.proteins,
            'carbs': daily_record.carbs,
            'fats': daily_record.fats,
            'streak': daily_record.streak
        }
            
        return jsonify({
            'success': True,
            'data': nutrition_data,
            'daily_total': daily_total
        })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/daily-stats')
def get_daily_stats():
    daily_record = get_or_create_daily_record()
    return jsonify({
        'calories': daily_record.calories,
        'proteins': daily_record.proteins,
        'carbs': daily_record.carbs,
        'fats': daily_record.fats,
        'streak': daily_record.streak
    })

@app.route('/update-streak', methods=['POST'])
def update_streak():
    daily_record = get_or_create_daily_record()
    daily_record.streak += 1
    db.session.commit()
    return jsonify({'success': True, 'streak': daily_record.streak})

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(debug=True) 
