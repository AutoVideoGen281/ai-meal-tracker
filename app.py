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
        # Get yesterday's record for streak calculation
        yesterday = DailyNutrition.query.filter(
            DailyNutrition.date < today
        ).order_by(DailyNutrition.date.desc()).first()
        
        # Calculate streak
        streak = yesterday.streak + 1 if yesterday else 1
        
        # Create new record for today
        record = DailyNutrition(
            date=today,
            calories=0,
            proteins=0,
            carbs=0,
            fats=0,
            streak=streak
        )
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
        prompt = f"""Analyze the meal in the attached image and estimate the macros and calories.
        
        Food name: {food_name}  
        {f'Quantity: {food_quantity}' if food_quantity else 'Look for size references in the image (coins, hands) to estimate portion size, or estimate yourself if no reference is found'} 
        
        make sure the results do fit these proportions:
        Protein = 4 kcal per gram
        Carbs = 4 kcal per gram
        Fats = 9 kcal per gram
        
        Return ONLY a valid JSON object with the following fields. All numbers must:  
        - Be in **raw number format** (e.g., 1000 instead of 1,000; 100 instead of 100.0)  
        - **Not** include any units like "kcal" or "g"  
        - **Not** be enclosed in quotes (they must be actual numerical values)  
        
        ```json
        {{
            "calories": 1000,
            "proteins": 20,
            "carbs": 50,
            "fats": 10
        }}
        """

        
        response = model.generate_content(
            contents=[
                prompt,
                {"mime_type": "image/jpeg", "data": img_byte_arr}
            ],
            generation_config={
                "max_output_tokens": 1024,
                "candidate_count": 1
            }
        )
        
        if response.prompt_feedback.block_reason:
            return jsonify({'success': False, 'error': 'Content blocked by safety settings'})
            
        response_text = response.text
        print("Raw Gemini response:", response_text)  # Debug print
        
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
            
            print("Cleaned JSON string:", json_str)  # Debug print
            nutrition_data = json.loads(json_str)
            print("Parsed nutrition data:", nutrition_data)  # Debug print
            
            # Validate the required fields
            required_fields = ['calories', 'proteins', 'carbs', 'fats']
            if not all(field in nutrition_data for field in required_fields):
                raise ValueError("Missing required nutritional fields")
                
            # Convert values to float and validate ranges
            max_values = {
                'calories': 1000,
                'proteins': 100,
                'carbs': 200,
                'fats': 100
            }
            
            for field in required_fields:
                value = float(nutrition_data.get(field, 0))
                # Ensure value is not negative
                value = max(0, value)
                # Ensure value doesn't exceed maximum
                value = min(value, max_values[field])
                # Round to 1 decimal place
                nutrition_data[field] = round(value, 1)
                
            print("Final nutrition data:", nutrition_data)  # Debug print
                
        except (json.JSONDecodeError, ValueError) as e:
            print("Error processing response:", str(e))  # Debug print
            return jsonify({'success': False, 'error': f'Invalid response format: {str(e)}'})
        
        # Update daily totals in database
        daily_record = get_or_create_daily_record()
        
        # Create new meal record first
        new_meal = Meal(
            date=date.today(),
            name=food_name,
            calories=nutrition_data['calories'],
            proteins=nutrition_data['proteins'],
            carbs=nutrition_data['carbs'],
            fats=nutrition_data['fats']
        )
        db.session.add(new_meal)
        
        # Calculate daily totals from all meals today
        today = date.today()
        today_meals = Meal.query.filter_by(date=today).all()
        
        # Reset daily totals
        daily_record.calories = sum(meal.calories for meal in today_meals) + nutrition_data['calories']
        daily_record.proteins = sum(meal.proteins for meal in today_meals) + nutrition_data['proteins']
        daily_record.carbs = sum(meal.carbs for meal in today_meals) + nutrition_data['carbs']
        daily_record.fats = sum(meal.fats for meal in today_meals) + nutrition_data['fats']
        
        db.session.commit()
            
        # Return both current meal and daily totals separately
        return jsonify({
            'success': True,
            'current_meal': {  # This is just the current meal's nutrition
                'name': food_name,
                'calories': nutrition_data['calories'],
                'proteins': nutrition_data['proteins'],
                'carbs': nutrition_data['carbs'],
                'fats': nutrition_data['fats']
            },
            'daily_total': {  # This is the running total for the day
                'calories': daily_record.calories,
                'proteins': daily_record.proteins,
                'carbs': daily_record.carbs,
                'fats': daily_record.fats,
                'streak': daily_record.streak
            }
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

@app.route('/reset-daily', methods=['POST'])
def reset_daily():
    daily_record = get_or_create_daily_record()
    daily_record.calories = 0
    daily_record.proteins = 0
    daily_record.carbs = 0
    daily_record.fats = 0
    db.session.commit()
    return jsonify({'success': True})

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(debug=True) 
