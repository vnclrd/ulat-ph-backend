from flask import Flask, request, jsonify
from flask_cors import CORS
from geopy.geocoders import Nominatim
from math import radians, sin, cos, sqrt, atan2
import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from supabase import create_client, Client

# Supabase setup
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': '*'}})

geolocator = Nominatim(user_agent='location_app')

# Config
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_client_ip():
    if request.headers.getlist('X-Forwarded-For'):
        return request.headers.getlist('X-Forwarded-For')[0]
    return request.remote_addr

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.route('/')
def home():
    return 'API is running'

# Reverse geocode endpoint
@app.route('/reverse-geocode', methods=['POST'])
def reverse_geocode():
    data = request.get_json()
    latitude = data.get('latitude')
    longitude = data.get('longitude')

    if latitude is None or longitude is None:
        return jsonify({'error': 'Latitude and longitude are required'}), 400

    try:
        location = geolocator.reverse((latitude, longitude), language='en')
        if not location:
            return jsonify({'error': 'Unable to get address'}), 400

        return jsonify({'address': location.address}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Geocode endpoint
@app.route('/geocode', methods=['POST'])
def geocode():
    data = request.get_json()
    address = data.get('address')

    if not address:
        return jsonify({'error': 'Address is required'}), 400

    try:
        location = geolocator.geocode(address)
        if location:
            return jsonify({
                'success': True,
                'location_name': location.address,
                'latitude': location.latitude,
                'longitude': location.longitude
            })
        else:
            return jsonify({'success': False, 'message': 'Location not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'Geocoding error: {str(e)}'}), 500

# Save manual location
@app.route('/save-location', methods=['POST'])
def save_location():
    data = request.get_json()
    location_name = data.get('location')

    if not location_name:
        return jsonify({'error': 'Location is required'}), 400

    try:
        location = geolocator.geocode(location_name)
        if not location:
            return jsonify({'error': 'Invalid location'}), 400

        location_data = {
            'name': location_name,
            'latitude': location.latitude,
            'longitude': location.longitude
        }
        return jsonify({'message': 'Location saved successfully', 'data': location_data}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Reports endpoint (GET + POST)
@app.route('/api/reports', methods=['GET', 'POST'])
def reports_handler():
    if request.method == 'GET':
        try:
            user_lat = request.args.get('latitude', type=float)
            user_lng = request.args.get('longitude', type=float)

            response = supabase.from_('reports').select('*').execute()
            all_reports = response.data

            if user_lat is not None and user_lng is not None:
                filtered_reports = [
                    report for report in all_reports
                    if haversine(user_lat, user_lng, report['latitude'], report['longitude']) <= 1
                ]
            else:
                filtered_reports = all_reports

            return jsonify({'success': True, 'reports': filtered_reports}), 200
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    elif request.method == 'POST':
        try:
            image_filename = None
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename != '' and allowed_file(file.filename):
                    file_extension = secure_filename(file.filename).rsplit('.', 1)[1].lower()
                    image_uuid = str(uuid.uuid4())
                    image_filename = f'{image_uuid}.{file_extension}'
                    supabase_upload_path = f'images/{image_filename}'
                    supabase.storage.from_('reports-images').upload(supabase_upload_path, file)

            issue_type = request.form.get('issueType', '')
            custom_issue = request.form.get('customIssue', '')
            description = request.form.get('description', '')
            location_name = request.form.get('location', '')
            location_lat = request.form.get('latitude', '')
            location_lng = request.form.get('longitude', '')

            response = supabase.from_('reports').insert({
                'issue_type': issue_type,
                'custom_issue': custom_issue if issue_type == 'custom' else None,
                'description': description,
                'location_name': location_name,
                'latitude': float(location_lat) if location_lat else None,
                'longitude': float(location_lng) if location_lng else None,
                'image_filename': image_filename
            }).execute()

            inserted_data = response.data
            if inserted_data:
                return jsonify({
                    'success': True,
                    'message': 'Report submitted successfully',
                    'report_id': inserted_data[0]['id']
                }), 201
            else:
                raise Exception('Supabase insertion failed.')
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error submitting report: {str(e)}'}), 500

# Sightings
@app.route('/api/reports/<report_id>/sightings', methods=['POST'])
def add_sighting(report_id):
    try:
        client_ip = get_client_ip()
        data = request.get_json()
        device_id = data.get('device_id')

        if not device_id:
            return jsonify({'success': False, 'message': 'Device ID is required'}), 400

        reports_response = supabase.from_('reports').select('sightings').eq('id', report_id).single().execute()
        sightings_data = reports_response.data.get('sightings') or {'count': 0, 'device_ids': [], 'user_ips': []}

        if device_id in sightings_data['device_ids'] or client_ip in sightings_data['user_ips']:
            return jsonify({'success': False, 'message': "You've already marked this sighting."}), 409

        sightings_data['count'] += 1
        sightings_data['device_ids'].append(device_id)
        sightings_data['user_ips'].append(client_ip)

        supabase.from_('reports').update({'sightings': sightings_data}).eq('id', report_id).execute()

        return jsonify({'success': True, 'message': "You've marked this as seen. Thank you!"})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error updating sighting: {str(e)}'}), 500

# Resolved
@app.route('/api/reports/<report_id>/resolved', methods=['POST'])
def add_resolved(report_id):
    try:
        client_ip = get_client_ip()
        data = request.get_json()
        device_id = data.get('device_id')

        if not device_id:
            return jsonify({'success': False, 'message': 'Device ID is required'}), 400

        reports_response = supabase.from_('reports').select('resolved').eq('id', report_id).single().execute()
        resolved_data = reports_response.data.get('resolved') or {'count': 0, 'device_ids': [], 'user_ips': []}

        if device_id in resolved_data['device_ids'] or client_ip in resolved_data['user_ips']:
            return jsonify({'success': False, 'message': "You've already marked this as resolved."}), 409

        resolved_data['count'] += 1
        resolved_data['device_ids'].append(device_id)
        resolved_data['user_ips'].append(client_ip)

        supabase.from_('reports').update({'resolved': resolved_data}).eq('id', report_id).execute()

        if resolved_data['count'] >= 5:
            supabase.from_('reports').delete().eq('id', report_id).execute()
            return jsonify({'success': True, 'message': 'Report deleted.', 'report_deleted': True})

        return jsonify({'success': True, 'message': "You've marked this as resolved.", 'report_deleted': False})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error updating resolved status: {str(e)}'}), 500

# Get user status
@app.route('/api/reports/<report_id>/user-status', methods=['GET'])
def get_user_status(report_id):
    try:
        client_ip = get_client_ip()
        device_id = request.args.get('device_id')

        report = supabase.from_('reports').select('sightings, resolved').eq('id', report_id).single().execute().data

        sightings = report.get('sightings', {})
        resolved = report.get('resolved', {})

        has_sighting_click = device_id in sightings.get('device_ids', []) or client_ip in sightings.get('user_ips', [])
        has_resolved_click = device_id in resolved.get('device_ids', []) or client_ip in resolved.get('user_ips', [])

        return jsonify({
            'success': True,
            'has_sighting_click': has_sighting_click,
            'has_resolved_click': has_resolved_click
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error fetching user status: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
