document.addEventListener('DOMContentLoaded', () => {
    const uploadArea = document.querySelector('.upload-area');
    const imageInput = document.getElementById('imageInput');
    const imagePreview = document.getElementById('imagePreview');
    const previewImg = document.getElementById('previewImg');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const analysisResults = document.getElementById('analysisResults');

    // Goal inputs
    const goalInputs = {
        calories: document.getElementById('caloriesGoal'),
        proteins: document.getElementById('proteinsGoal'),
        carbs: document.getElementById('carbsGoal'),
        fats: document.getElementById('fatsGoal')
    };

    // Daily goals (will be updated from inputs)
    let DAILY_GOALS = {
        calories: parseInt(goalInputs.calories.value) || 2000,
        proteins: parseInt(goalInputs.proteins.value) || 50,
        carbs: parseInt(goalInputs.carbs.value) || 250,
        fats: parseInt(goalInputs.fats.value) || 70
    };

    // Add event listeners to goal inputs
    Object.entries(goalInputs).forEach(([nutrient, input]) => {
        input.addEventListener('change', () => {
            const value = parseInt(input.value) || 0;
            DAILY_GOALS[nutrient] = value;
            // Update progress bars with new goals
            fetch('/daily-stats')
                .then(response => response.json())
                .then(data => {
                    updateDailySummary(data);
                });
        });
    });

    // Save goals to localStorage
    function saveGoals() {
        localStorage.setItem('dailyGoals', JSON.stringify(DAILY_GOALS));
    }

    // Load goals from localStorage
    function loadGoals() {
        const savedGoals = localStorage.getItem('dailyGoals');
        if (savedGoals) {
            DAILY_GOALS = JSON.parse(savedGoals);
            // Update input fields
            Object.entries(DAILY_GOALS).forEach(([nutrient, value]) => {
                if (goalInputs[nutrient]) {
                    goalInputs[nutrient].value = value;
                }
            });
        }
    }

    // Load saved goals on startup
    loadGoals();

    // Save goals when changed
    Object.values(goalInputs).forEach(input => {
        input.addEventListener('change', saveGoals);
    });

    // Drag and drop functionality
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('border-blue-500');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('border-blue-500');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('border-blue-500');
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            handleImageUpload(file);
        }
    });

    uploadArea.addEventListener('click', () => {
        imageInput.click();
    });

    imageInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            handleImageUpload(file);
        }
    });

    function handleImageUpload(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            previewImg.src = e.target.result;
            imagePreview.classList.remove('hidden');
            uploadArea.classList.add('hidden');
        };
        reader.readAsDataURL(file);
    }

    // Analyze button click handler
    analyzeBtn.addEventListener('click', async () => {
        const file = imageInput.files[0];
        if (!file) return;
        
        // Show loading state
        analyzeBtn.disabled = true;
        analyzeBtn.innerHTML = '<span class="inline-block animate-spin mr-2">⏳</span> Analyzing...';

        try {
            const formData = new FormData();
            formData.append('image', file);

            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.success) {
                updateAnalysisDisplay(data.data);
                updateDailySummary(data.daily_total);
                analysisResults.classList.remove('hidden');
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            alert('Error analyzing image: ' + error.message);
        } finally {
            // Reset button state
            analyzeBtn.disabled = false;
            analyzeBtn.textContent = 'Analyze Meal';
            
            // Reset upload area
            imagePreview.classList.add('hidden');
            uploadArea.classList.remove('hidden');
            previewImg.src = '';
            imageInput.value = '';
        }
    });

    function updateAnalysisDisplay(data) {
        document.getElementById('calories').textContent = Math.round(data.calories);
        document.getElementById('proteins').textContent = Math.round(data.proteins) + 'g';
        document.getElementById('carbs').textContent = Math.round(data.carbs) + 'g';
        document.getElementById('fats').textContent = Math.round(data.fats) + 'g';
    }

    function updateDailySummary(data) {
        // Update progress bars for all nutrients
        Object.entries(DAILY_GOALS).forEach(([nutrient, goal]) => {
            updateProgressBar(nutrient, data[nutrient], goal);
        });

        // Update streak
        document.getElementById('streakCount').textContent = `Streak: ${data.streak} days`;

        // Check if daily goals are met
        const allGoalsMet = Object.entries(DAILY_GOALS).every(
            ([nutrient, goal]) => data[nutrient] >= goal
        );

        if (allGoalsMet) {
            fetch('/update-streak', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    document.getElementById('streakCount').textContent = `Streak: ${data.streak} days`;
                });
        }
    }

    function updateProgressBar(nutrient, current, goal) {
        const percentage = Math.min((current / goal) * 100, 100);
        const progressBar = document.getElementById(`${nutrient}Bar`);
        const progressText = document.getElementById(`${nutrient}Progress`);
        
        if (progressBar && progressText) {
            progressBar.style.width = `${percentage}%`;
            progressText.textContent = `${Math.round(current)}/${goal}${nutrient !== 'calories' ? 'g' : ''}`;
        }
    }

    // Load initial daily stats
    fetch('/daily-stats')
        .then(response => response.json())
        .then(data => {
            updateDailySummary(data);
        });
}); 