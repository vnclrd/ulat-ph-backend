from flask import Flask, request, jsonify
from flask_cors import CORS
from math import radians, sin, cos, sqrt, atan2
from supabase import create_client, Client
from PIL import Image
from io import BytesIO
from functools import lru_cache
import os
import uuid
import re
import requests

# ============================== CONFIGURATION ==============================
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': '*'}})

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# ============================== UTILITY FUNCTIONS ==============================
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def resize_image(image_file, size=(800, 600), quality=85):
    try:
        image_file.seek(0)
        img = Image.open(image_file)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(size, Image.Resampling.LANCZOS)
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=quality)
        img_byte_arr.seek(0)
        return img_byte_arr
    except Exception as e:
        raise Exception(f"Image processing error: {str(e)}")

def delete_image_from_storage(image_filename):
    try:
        supabase.storage.from_('reports-images').remove([f'images/{image_filename}'])
    except Exception as e:
        print(f"Error deleting image: {e}", flush=True)

def update_report_counter(report_id, field):
    response = supabase.from_('reports').select(field).eq('id', report_id).single().execute()
    if not response.data:
        return None, 'Report not found'

    counter = response.data.get(field, {'count': 0})
    counter['count'] = counter.get('count', 0) + 1

    supabase.from_('reports').update({field: counter}).eq('id', report_id).execute()
    return counter, None

# ============================== GEOCODING HELPERS ==============================
def is_coordinates(text: str):
    """Checks if the query is a 'lat, lon' coordinate pair."""
    pattern = r'^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$'
    return re.match(pattern, text.strip()) is not None

def reverse_lookup(lat: float, lon: float):
    """
    Reverse geocodes coordinates to a Philippine city/barangay using BigDataCloud
    (Free, no shared-IP bans on Render).
    """
    try:
        url = "https://api.bigdatacloud.net/data/reverse-geocode-client"
        params = {"latitude": lat, "longitude": lon, "localityLanguage": "en"}
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            data = res.json()
            locality = data.get("locality") or data.get("city") or data.get("principalSubdivision")
            country = data.get("countryName", "")
            return f"{locality}, {country}" if locality else f"{lat:.4f}, {lon:.4f}"
    except Exception as e:
        print(f"BigDataCloud reverse error: {e}", flush=True)

    return f"{lat:.4f}, {lon:.4f}"

@lru_cache(maxsize=500)
def fetch_geocode(address: str):
    clean_addr = address.strip()

    # Case A: If user entered or GPS passed raw coordinates: "14.4634, 120.9950"
    if is_coordinates(clean_addr):
        lat_str, lon_str = clean_addr.split(",")
        lat, lon = float(lat_str.strip()), float(lon_str.strip())
        display_name = reverse_lookup(lat, lon)
        return {
            "location_name": display_name,
            "latitude": lat,
            "longitude": lon
        }

    # Case B: User searched by city/province name -> Use Open-Meteo
    try:
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": clean_addr, "count": 1, "language": "en", "format": "json"}
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results")
            if results and len(results) > 0:
                top = results[0]
                name_parts = [top.get("name"), top.get("admin1"), top.get("country")]
                display_name = ", ".join([p for p in name_parts if p])
                return {
                    "location_name": display_name,
                    "latitude": top.get("latitude"),
                    "longitude": top.get("longitude")
                }
    except Exception as err:
        print(f"Open-Meteo search error: {err}", flush=True)

    return None

# ============================== ROUTES ==============================
@app.route('/')
def home():
    return 'Hello, World!'

# ------------------------------ Geocoding Routes ------------------------------
@app.route('/reverse-geocode', methods=['POST'])
def reverse_geocode():
    data = request.get_json() or {}
    latitude, longitude = data.get('latitude'), data.get('longitude')

    if latitude is None or longitude is None:
        return jsonify({'error': 'Latitude and longitude are required'}), 400

    try:
        lat, lon = float(latitude), float(longitude)
        address = reverse_lookup(lat, lon)
        return jsonify({'address': address}), 200
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid coordinates'}), 400
    except Exception as e:
        print(f"Error during reverse geocoding: {str(e)}", flush=True)
        return jsonify({'error': 'Geocoding service error', 'fallback_address': f'{latitude}, {longitude}'}), 500

@app.route('/geocode', methods=['POST'])
def geocode():
    data = request.get_json() or {}
    address = data.get('address')
    if not address:
        return jsonify({'error': 'Address is required'}), 400

    try:
        location = fetch_geocode(address)
        if location:
            return jsonify({
                'success': True,
                'location_name': location['location_name'],
                'latitude': location['latitude'],
                'longitude': location['longitude']
            }), 200

        return jsonify({'success': False, 'message': 'Location not found'}), 404

    except Exception as e:
        print(f"Geocoding exception: {str(e)}", flush=True)
        return jsonify({'success': False, 'message': f'Geocoding error: {str(e)}'}), 500

# ------------------------------ Reports Routes ------------------------------
@app.route('/api/reports', methods=['GET'])
def get_reports():
    try:
        user_lat = request.args.get('latitude', type=float)
        user_lng = request.args.get('longitude', type=float)
        response = supabase.from_('reports').select('*').execute()
        all_reports = response.data or []

        if user_lat is not None and user_lng is not None:
            all_reports = [r for r in all_reports if r.get('latitude') and r.get('longitude') and haversine(user_lat, user_lng, r['latitude'], r['longitude']) <= 1]
        all_reports.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        return jsonify({'success': True, 'reports': all_reports}), 200
    except Exception as e:
        print(f"Error fetching reports: {str(e)}", flush=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/reports', methods=['POST'])
def create_report():
    try:
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                resized_image_bytes = resize_image(file)
                image_filename = f"{uuid.uuid4()}.{file.filename.rsplit('.', 1)[1].lower()}"
                supabase.storage.from_('reports-images').upload(
                    path=f'images/{image_filename}',
                    file=resized_image_bytes.getvalue(),
                    file_options={'content-type': 'image/jpeg'}
                )

        issue_type = request.form.get('issueType', '')
        custom_issue = request.form.get('customIssue', '')
        description = request.form.get('description', '')
        location_name = request.form.get('location', '')
        location_lat = request.form.get('latitude', '')
        location_lng = request.form.get('longitude', '')

        if not issue_type or not location_name or not location_lat or not location_lng:
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        report_data = {
            'issue_type': issue_type,
            'custom_issue': custom_issue if issue_type == 'custom' else None,
            'description': description,
            'location_name': location_name,
            'latitude': float(location_lat),
            'longitude': float(location_lng),
            'image_filename': image_filename,
            'sightings': {'count': 0},
            'resolved': {'count': 0}
        }

        response = supabase.from_('reports').insert(report_data).execute()
        if response.data:
            return jsonify({'success': True, 'message': 'Report submitted successfully', 'report_id': response.data[0]['id']}), 201

        raise Exception('Supabase insertion failed - no data returned')
    except Exception as e:
        print(f"Error creating report: {str(e)}", flush=True)
        return jsonify({'success': False, 'message': f'Error submitting report: {str(e)}'}), 500

@app.route('/api/reports/<report_id>', methods=['GET'])
def get_report(report_id):
    try:
        response = supabase.from_('reports').select('*').eq('id', report_id).single().execute()
        if response.data:
            return jsonify({'success': True, 'report': response.data})
        return jsonify({'success': False, 'message': 'Report not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error fetching report: {str(e)}'}), 500

@app.route('/api/reports/<report_id>/sightings', methods=['POST'])
def add_sighting(report_id):
    counter, error = update_report_counter(report_id, 'sightings')
    if error:
        return jsonify({'success': False, 'message': error}), 409 if error == 'Already marked' else 404
    return jsonify({'success': True, 'message': "You've seen this too. Thank you!"})

@app.route('/api/reports/<report_id>/resolved', methods=['POST'])
def add_resolved(report_id):
    counter, error = update_report_counter(report_id, 'resolved')
    if error:
        return jsonify({'success': False, 'message': error}), 409 if error == 'Already marked' else 404

    if counter['count'] >= 5:
        report_response = supabase.from_('reports').select('image_filename').eq('id', report_id).single().execute()
        image_filename = report_response.data.get('image_filename') if report_response.data else None
        supabase.from_('reports').delete().eq('id', report_id).execute()
        if image_filename:
            delete_image_from_storage(image_filename)
        return jsonify({'success': True, 'message': 'Report resolved and deleted.', 'report_deleted': True})

    return jsonify({'success': True, 'message': "You've marked this as resolved. Thank you!", 'report_deleted': False})

@app.route('/api/reports/<report_id>/user-status', methods=['GET'])
def get_user_status(report_id):
    try:
        response = supabase.from_('reports').select('sightings, resolved').eq('id', report_id).single().execute()
        if not response.data:
            return jsonify({'success': False, 'message': 'Report not found'}), 404

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error fetching user status: {str(e)}'}), 500

@app.route('/api/reports/<report_id>', methods=['DELETE'])
def delete_report(report_id):
    try:
        response = supabase.from_('reports').select('image_filename').eq('id', report_id).single().execute()
        if not response.data:
            return jsonify({'success': False, 'message': 'Report not found'}), 404

        image_filename = response.data.get('image_filename')
        supabase.from_('reports').delete().eq('id', report_id).execute()
        if image_filename:
            delete_image_from_storage(image_filename)
        return jsonify({'success': True, 'message': 'Report deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error deleting report: {str(e)}'}), 500

# ============================== RUN ==============================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)