import streamlit as st
import pandas as pd
import joblib
import numpy as np
import requests
import json
import re
import time
from datetime import datetime

firebase_config = {
  "apiKey": "AIzaSyDPEsepXURaLo-Pz3S-NECYsO1vPGKYEqM",
  "authDomain": "chlorowatch.firebaseapp.com",
  "databaseURL": "https://chlorowatch-default-rtdb.asia-southeast1.firebasedatabase.app",
  "projectId": "chlorowatch",
  "storageBucket": "chlorowatch.firebasestorage.app",
  "messagingSenderId": "1079557994846",
  "appId": "1:1079557994846:web:dcb2513116b13feff437cf",
  "measurementId": "G-HHJMQ7F6V8"
}
# Firebase Realtime Database URL
database_url = firebase_config['databaseURL']

# Load the stacking model and scaler
stacking_model_loaded = joblib.load('stacking_ensemble_model.joblib')
scaler_loaded = joblib.load('scaler.pkl')

# Streamlit app title
st.title('Chlorophyll Prediction and Forecasting Model')

# Function to sanitize Firebase keys
def sanitize_key(key):
    """Sanitize Firebase keys to remove invalid characters."""
    return re.sub(r'[$#\[\]/.]', '_', key)

# Recursive function to sanitize nested dictionaries
def sanitize_dict(d):
    """Recursively sanitize keys in a nested dictionary."""
    if isinstance(d, dict):
        return {sanitize_key(k): sanitize_dict(v) for k, v in d.items()}
    else:
        return d

# Function to fetch the latest sensor data from Firebase using the REST API
def get_latest_sensor_data():
    try:
        sensor_data_ref = f'{database_url}/SensorData.json'
        response = requests.get(sensor_data_ref)
        
        if response.status_code == 200:
            sensor_data = response.json()
            # Sort the data by timestamp and get the latest entry
            latest_data = max(sensor_data.values(), key=lambda x: x['timestamp'])
            
            # Sanitize the data and extract timestamp
            sanitized_data = sanitize_dict(latest_data)
            timestamp = datetime.fromtimestamp(sanitized_data['timestamp'] / 1000.0)

            # Prepare the sensor data as a DataFrame
            sensor_df = pd.DataFrame({
                'Temp (°C)': [sanitized_data['Temperature']],
                'Turbidity (FNU)': [sanitized_data['Turbidity']],
                'pH': [sanitized_data['pH']],
                'DO (mg/L)': [sanitized_data['DissolvedOxygen']],
                'year': [timestamp.year],
                'month': [timestamp.month],
                'day': [timestamp.day],
                'day_of_week': [timestamp.weekday()],
                'day_of_year': [timestamp.timetuple().tm_yday],
                'quarter': [(timestamp.month - 1) // 3 + 1],
                'hour': [timestamp.hour]
            })
            return sensor_df, timestamp
        else:
            st.write(f"Error fetching data from Firebase: {response.text}")
            return pd.DataFrame(), None
    except Exception as e:
        st.write(f"Error fetching data from Firebase: {e}")
        return pd.DataFrame(), None

# Forecast future chlorophyll levels based on the latest data
def forecast_future_chlorophyll(df, stacking_model, scaler, features, steps=10, uncertainty_factor=0.1):
    last_row = df[features].iloc[-1:].copy()

    forecasts = []
    upper_bounds = []
    lower_bounds = []

    for i in range(steps):
        # Simulate a "next" time step (roll over the day/month)
        last_row['day_of_year'] += 1
        last_row['day'] = (last_row['day'] % 31) + 1
        last_row['day_of_week'] = (last_row['day_of_week'] + 1) % 7
        if last_row['day'].iloc[0] == 1:
            last_row['month'] = (last_row['month'] % 12) + 1
            if last_row['month'].iloc[0] == 1:
                last_row['year'] += 1
        last_row['quarter'] = (last_row['month'] - 1) // 3 + 1

        # Scaling and prediction
        last_row_scaled = scaler.transform(last_row)
        forecast_log = stacking_model.predict(last_row_scaled)
        forecast = np.expm1(forecast_log)  # Reverse log transformation

        # Calculate the upper and lower bounds
        upper_bound = forecast[0] * (1 + uncertainty_factor)  # Add 10% to forecast
        lower_bound = forecast[0] * (1 - uncertainty_factor)  # Subtract 10% from forecast

        forecasts.append(forecast[0])
        upper_bounds.append(upper_bound)
        lower_bounds.append(lower_bound)

    return forecasts, upper_bounds, lower_bounds

# Save predictions and forecasts to Firebase using the REST API
def save_prediction_to_firebase(predicted_chlorophyll, sensor_data, forecast_values, upper_bounds, lower_bounds):
    predicted_chlorophyll = float(predicted_chlorophyll)
    
    # Use only the first value from forecast, upper bound, and lower bound arrays
    first_forecast_value = float(forecast_values[0])
    first_upper_bound = float(upper_bounds[0])
    first_lower_bound = float(lower_bounds[0])

    # Sanitize sensor data keys
    sensor_data_values = {
        sanitize_key(key): float(value[0]) if isinstance(value[0], np.float64) else int(value[0])
        for key, value in sensor_data.items()
    }

    # Push the prediction and forecast data to Firebase
    prediction_ref = f'{database_url}/Predictions.json'
    timestamp = (pd.Timestamp.now() + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    data = {
        **sensor_data_values,
        'Predicted_Chlorophyll': predicted_chlorophyll,
        'Forecasted_Chlorophyll': first_forecast_value,
        'Upper_Bound_Chlorophyll': first_upper_bound,
        'Lower_Bound_Chlorophyll': first_lower_bound,
        'timestamp': timestamp
    }
    response = requests.post(prediction_ref, json=data)

    if response.status_code != 200:
        st.write(f"Error saving prediction: {response.text}")

    # Update the current prediction separately
    current_prediction_ref = f'{database_url}/CurrentPrediction.json'
    current_data = {
        'Predicted_Chlorophyll': predicted_chlorophyll,
    }
    # Make a PUT request to update the current prediction
    current_response = requests.put(current_prediction_ref, json=current_data)

    if current_response.status_code != 200:
        st.write(f"Error updating current prediction: {current_response.text}")

# Main app logic
if __name__ == '__main__':
    st.write("## Fetching and Predicting Chlorophyll Values...")

    # Placeholder for DataFrame display
    table_placeholder = st.empty()

    # Initialize a DataFrame for sensor data and forecast columns
    sensor_data_display = pd.DataFrame(columns=['Temp (°C)', 'Turbidity (FNU)', 'pH', 'DO (mg/L)', 'Predicted_Chlorophyll (ug/L)', 
                                               'Forecasted_Chlorophyll (ug/L)', 'Upper Bound (ug/L)', 'Lower Bound (ug/L)'])

    # Continuous monitoring of Firebase
    last_timestamp = None

    while True:
        # Fetch the latest sensor data
        fetched_sensor_data, timestamp = get_latest_sensor_data()

        if not fetched_sensor_data.empty:
            # Check if the timestamp is new (to avoid redundant processing)
            if timestamp != last_timestamp:
                last_timestamp = timestamp  # Update last timestamp

                # Define the features used for prediction
                features = ['Temp (°C)', 'Turbidity (FNU)', 'pH', 'DO (mg/L)', 'year', 'month', 'day', 'day_of_week', 'day_of_year', 'quarter', 'hour']
                X_new_scaled = scaler_loaded.transform(fetched_sensor_data[features])

                # Prediction
                y_new_pred_log = stacking_model_loaded.predict(X_new_scaled)
                y_new_pred = np.expm1(y_new_pred_log)  # Reverse the log transformation

                # Forecast the next 10 intervals, with upper and lower bounds
                future_forecasts, upper_bounds, lower_bounds = forecast_future_chlorophyll(fetched_sensor_data, stacking_model_loaded, scaler_loaded, features, steps=10)

                # Save the prediction and forecast to Firebase
                save_prediction_to_firebase(y_new_pred[0], fetched_sensor_data, future_forecasts, upper_bounds, lower_bounds)

                # Update the sensor data display DataFrame
                sensor_data_display = pd.DataFrame({
                    'Temp (°C)': fetched_sensor_data['Temp (°C)'],
                    'Turbidity (FNU)': fetched_sensor_data['Turbidity (FNU)'],
                    'pH': fetched_sensor_data['pH'],
                    'DO (mg/L)': fetched_sensor_data['DO (mg/L)'],
                    'Predicted_Chlorophyll (ug/L)': y_new_pred[0],
                    'Forecasted_Chlorophyll (ug/L)': future_forecasts[0],  # Take the first forecast as example
                    'Upper Bound (ug/L)': upper_bounds[0],
                    'Lower Bound (ug/L)': lower_bounds[0]
                })

                # Display the updated data
                table_placeholder.dataframe(sensor_data_display)

        else:
            st.write("No sensor data found. Please check your Firebase database.")

        # Wait for a short time before checking for new data again
        time.sleep(5)  # Adjust time interval as necessary
