from flask import Flask, request, jsonify
from flask_cors import CORS
from geopy.geocoders import Nominatim
from math import radians, sin, cos, sqrt, atan2

# Imports for file and information saving
import os
import json
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from supabase import create_client, Client

# Imports for image size reducer
from PIL import Image
from io import BytesIO

# Supabase credentials
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': '*'}})

geolocator = Nominatim(user_agent='location_app')

# Configuration for file uploads
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def get_client_ip():
    '''Get the client's IP address'''
    if request.headers.getlist('X-Forwarded-For'):
        ip = request.headers.getlist('X-Forwarded-For')[0]
    else:
        ip = request.remote_addr
    return ip

def haversine(lat1, lon1, lat2, lon2):
    '''Calculate the distance between two points on Earth using the Haversine formula.'''
    R = 6371  # Radius of Earth in kilometers
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def resize_image(image_file, size=(800, 600), quality=85):
    '''Resize image and return BytesIO object'''
    try:
        # Reset file pointer to beginning
        image_file.seek(0)
        
        img = Image.open(image_file)
        
        # Convert to RGB if necessary (for JPEG compatibility)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        img.thumbnail(size, Image.Resampling.LANCZOS)
        
        # Save the resized image to a temporary in-memory buffer
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=quality)
        img_byte_arr.seek(0)
        
        return img_byte_arr
    except Exception as e:
        raise Exception(f"Image processing error: {str(e)}")

@app.route('/')
def home():
    return 'Hello, World!'

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

        address = location.address
        return jsonify({'address': address}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/geocode', methods=['POST'])
def geocode():
    '''Convert an address to latitude and longitude'''
    data = request.get_json()
    address = data.get('address')
    
    if not address:
        return jsonify({'error': 'Address is required'}), 400

    try:
        location_data = geolocator.geocode(address)
        
        if location_data:
            return jsonify({
                'success': True,
                'location_name': location_data.address,
                'latitude': location_data.latitude,
                'longitude': location_data.longitude
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Location not found'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Geocoding error: {str(e)}'
        }), 500

@app.route('/save-location', methods=['POST'])
def save_location():
    data = request.get_json()
    location_name = data.get('location')
    print('Received location:', location_name, flush=True)

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

        print('Saved location:', location_data, flush=True)
        return jsonify({'message': 'Location saved successfully', 'data': location_data}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/reports', methods=['GET'])
def get_reports():
    '''Get all reports with optional filtering by location'''
    try:
        # Get latitude and longitude from query parameters
        user_lat = request.args.get('latitude', type=float)
        user_lng = request.args.get('longitude', type=float)

        # Retrieve all reports from Supabase
        response = supabase.from_('reports').select('*').execute()
        
        if not response.data:
            return jsonify({'success': True, 'reports': []}), 200
            
        all_reports = response.data

        # Filter reports based on distance (within 1km)
        if user_lat is not None and user_lng is not None:
            filtered_reports = []
            for report in all_reports:
                if (report.get('latitude') is not None and 
                    report.get('longitude') is not None):
                    distance = haversine(user_lat, user_lng, 
                                       report['latitude'], report['longitude'])
                    if distance <= 1:  # Within 1km
                        filtered_reports.append(report)
            reports = filtered_reports
        else:
            reports = all_reports

        # Sort by creation time (newest first)
        reports.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        return jsonify({'success': True, 'reports': reports}), 200

    except Exception as e:
        print(f"Error fetching reports: {str(e)}", flush=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/reports', methods=['POST'])
def create_report():
    '''Create a new report'''
    try:
        print("Starting report creation...", flush=True)
        
        # Handle image upload and resizing
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                try:
                    print("Processing image...", flush=True)
                    
                    # Process and resize the image
                    resized_image_bytes = resize_image(file)

                    # Generate a unique filename
                    file_extension = secure_filename(file.filename).rsplit('.', 1)[1].lower()
                    image_uuid = str(uuid.uuid4())
                    image_filename = f'{image_uuid}.{file_extension}'
                    
                    print(f"Uploading image: {image_filename}", flush=True)
                    
                    # Upload the resized image to Supabase Storage
                    supabase_upload_path = f'images/{image_filename}'
                    upload_response = supabase.storage.from_('reports-images').upload(
                        file=resized_image_bytes.getvalue(),
                        path=supabase_upload_path,
                        file_options={'content-type': 'image/jpeg'}
                    )
                    
                    print("Image uploaded successfully", flush=True)
                    
                except Exception as e:
                    print(f"Image upload error: {str(e)}", flush=True)
                    return jsonify({
                        'success': False,
                        'message': f'Error uploading image: {str(e)}'
                    }), 500

        # Get form data
        issue_type = request.form.get('issueType', '')
        custom_issue = request.form.get('customIssue', '')
        description = request.form.get('description', '')
        location_name = request.form.get('location', '')
        location_lat = request.form.get('latitude', '')
        location_lng = request.form.get('longitude', '')
        
        print(f"Form data received: {issue_type}, {location_name}", flush=True)
        
        # Validate required fields
        if not issue_type or not location_name or not location_lat or not location_lng:
            return jsonify({
                'success': False,
                'message': 'Missing required fields'
            }), 400
        
        # Prepare data for insertion
        report_data = {
            'issue_type': issue_type,
            'custom_issue': custom_issue if issue_type == 'custom' else None,
            'description': description,
            'location_name': location_name,
            'latitude': float(location_lat),
            'longitude': float(location_lng),
            'image_filename': image_filename,
            'sightings': {'count': 0, 'user_ips': []},
            'resolved': {'count': 0, 'user_ips': []}
        }
        
        print("Inserting into Supabase...", flush=True)
        
        # Insert data into Supabase table
        response = supabase.from_('reports').insert(report_data).execute()
        
        # Check if insertion was successful
        if response.data and len(response.data) > 0:
            print("Report created successfully", flush=True)
            return jsonify({
                'success': True,
                'message': 'Report submitted successfully',
                'report_id': response.data[0]['id']
            }), 201
        else:
            raise Exception('Supabase insertion failed - no data returned')

    except Exception as e:
        print(f"Error creating report: {str(e)}", flush=True)
        return jsonify({
            'success': False,
            'message': f'Error submitting report: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>', methods=['GET'])
def get_report(report_id):
    '''Get a specific report by ID'''
    try:
        response = supabase.from_('reports').select('*').eq('id', report_id).single().execute()
        
        if response.data:
            return jsonify({
                'success': True,
                'report': response.data
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error fetching report: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/sightings', methods=['POST'])
def add_sighting(report_id):
    '''Add a sighting to a report'''
    try:
        client_ip = get_client_ip()

        # Get the current report
        reports_response = supabase.from_('reports').select('sightings').eq('id', report_id).single().execute()
        
        if not reports_response.data:
            return jsonify({'success': False, 'message': 'Report not found'}), 404
            
        sightings_data = reports_response.data.get('sightings')

        # Initialize data if not present
        if not isinstance(sightings_data, dict):
            sightings_data = {'count': 0, 'user_ips': []}

        # Check if user already reported sighting
        if client_ip in sightings_data.get('user_ips', []):
            return jsonify({
                'success': False,
                'message': "You've already seen this issue."
            }), 409

        # Update sightings
        sightings_data['count'] = sightings_data.get('count', 0) + 1
        sightings_data['user_ips'] = sightings_data.get('user_ips', [])
        sightings_data['user_ips'].append(client_ip)

        # Update in database
        supabase.from_('reports').update({'sightings': sightings_data}).eq('id', report_id).execute()

        return jsonify({
            'success': True,
            'message': "You've seen this too. Thank you for your contribution!"
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error updating sighting: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/resolved', methods=['POST'])
def add_resolved(report_id):
    '''Mark a report as resolved'''
    try:
        client_ip = get_client_ip()

        # Get the current report
        reports_response = supabase.from_('reports').select('resolved').eq('id', report_id).single().execute()
        
        if not reports_response.data:
            return jsonify({'success': False, 'message': 'Report not found'}), 404
            
        resolved_data = reports_response.data.get('resolved')
        
        # Initialize data if not present
        if not isinstance(resolved_data, dict):
            resolved_data = {'count': 0, 'user_ips': []}

        # Check if user already marked as resolved
        if client_ip in resolved_data.get('user_ips', []):
            return jsonify({
                'success': False,
                'message': "You've already said that this was resolved."
            }), 409
        
        # Update resolved status
        resolved_data['count'] = resolved_data.get('count', 0) + 1
        resolved_data['user_ips'] = resolved_data.get('user_ips', [])
        resolved_data['user_ips'].append(client_ip)

        # Update in database
        supabase.from_('reports').update({'resolved': resolved_data}).eq('id', report_id).execute()

        # Check if report should be deleted (5 or more resolved votes)
        if resolved_data['count'] >= 5:
            # Get image filename before deletion
            report_response = supabase.from_('reports').select('image_filename').eq('id', report_id).single().execute()
            image_filename = report_response.data.get('image_filename') if report_response.data else None
            
            # Delete the report
            supabase.from_('reports').delete().eq('id', report_id).execute()
            
            # Clean up image if it exists
            if image_filename:
                try:
                    supabase.storage.from_('reports-images').remove([f'images/{image_filename}'])
                except Exception as img_error:
                    print(f'Error deleting image: {img_error}', flush=True)
            
            return jsonify({
                'success': True,
                'message': 'Report marked as resolved and deleted.',
                'report_deleted': True
            })

        return jsonify({
            'success': True,
            'message': "You've said that this was resolved. Thank you for your contribution!",
            'report_deleted': False
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error updating resolved status: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/user-status', methods=['GET'])
def get_user_status(report_id):
    '''Check if user has already interacted with this report'''
    try:
        client_ip = get_client_ip()

        # Get the report to check sightings and resolved data
        response = supabase.from_('reports').select('sightings, resolved').eq('id', report_id).single().execute()
        
        if not response.data:
            return jsonify({'success': False, 'message': 'Report not found'}), 404
        
        report_data = response.data
        sightings_data = report_data.get('sightings', {})
        resolved_data = report_data.get('resolved', {})
        
        has_sighting_click = client_ip in sightings_data.get('user_ips', [])
        has_resolved_click = client_ip in resolved_data.get('user_ips', [])
        
        return jsonify({
            'success': True,
            'has_sighting_click': has_sighting_click,
            'has_resolved_click': has_resolved_click
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error fetching user status: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>', methods=['DELETE'])
def delete_report(report_id):
    '''Delete a report'''
    try:
        # Get the report to find associated image
        response = supabase.from_('reports').select('image_filename').eq('id', report_id).single().execute()
        
        if not response.data:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        image_filename = response.data.get('image_filename')
        
        # Delete the report from database
        delete_response = supabase.from_('reports').delete().eq('id', report_id).execute()
        
        # Delete associated image file from Supabase Storage if it exists
        if image_filename:
            try:
                supabase.storage.from_('reports-images').remove([f'images/{image_filename}'])
            except Exception as e:
                print(f'Error deleting image from Supabase: {e}', flush=True)
        
        return jsonify({
            'success': True,
            'message': 'Report deleted successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error deleting report: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5000)