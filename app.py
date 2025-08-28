from flask import Flask, request, jsonify
from flask_cors import CORS
from geopy.geocoders import Nominatim

# Imports for file and information saving
import os
import json
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from supabase import create_client, Client

# Supabase credentials (you will get these from your Supabase dashboard)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}) # resources for testing

geolocator = Nominatim(user_agent="location_app")

# Configuration for file uploads and data storage
UPLOAD_FOLDER = 'uploads/images'
DATA_FILE = 'data/reports.json'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('data', exist_ok=True)

def get_client_ip():
    """Get the client's IP address"""
    if request.headers.getlist("X-Forwarded-For"):
        ip = request.headers.getlist("X-Forwarded-For")[0]
    else:
        ip = request.remote_addr
    return ip

@app.route('/')
def home():
    return 'Hello, World!'

# Automatic detect current location
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
    """Convert an address to latitude and longitude"""
    data = request.get_json()
    address = data.get('address')
    
    if not address:
        return jsonify({'error': 'Address is required'}), 400

    try:
        # Use geolocator to find the location details from the address
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

# Manual entering of location
@app.route('/save-location', methods=['POST'])
def save_location():
    data = request.get_json()
    location_name = data.get('location')
    print('Received location:', location_name, flush=True)

    if not location_name:
        return jsonify({'error': 'Location is required'}), 400

    try:
        # Get coordinates from location name
        location = geolocator.geocode(location_name)
        if not location:
            return jsonify({'error': 'Invalid location'}), 400

        location_data = {
            'name': location_name,
            'latitude': location.latitude,
            'longitude': location.longitude
        }

        print("Saved location:", location_data, flush=True)
        return jsonify({'message': 'Location saved successfully', 'data': location_data}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# FILE SAVING COMPONENTS
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_reports():
    """Load reports from JSON file"""
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reports(reports):
    """Save reports to JSON file"""
    with open(DATA_FILE, 'w') as f:
        json.dump(reports, f, indent=2)

@app.route('/api/reports', methods=['POST'])
def create_report():
    try:
        # Handle image upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                try:
                    # Generate a unique filename using UUID
                    file_extension = secure_filename(file.filename).rsplit('.', 1)[1].lower()
                    image_uuid = str(uuid.uuid4())
                    image_filename = f"{image_uuid}.{file_extension}"
                    
                    # Upload file to Supabase Storage
                    supabase_upload_path = f"images/{image_filename}"
                    supabase.storage.from_("reports-images").upload(
                        file=file.read(),
                        path=supabase_upload_path,
                        file_options={"content-type": file.content_type}
                    )
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'message': f'Error uploading image to Supabase: {str(e)}'
                    }), 500

        # Get form data
        issue_type = request.form.get('issueType', '')
        custom_issue = request.form.get('customIssue', '')
        description = request.form.get('description', '')
        location_name = request.form.get('location', '')
        location_lat = request.form.get('latitude', '')
        location_lng = request.form.get('longitude', '')
        
        # Insert data into Supabase table
        data, count = supabase.from_("reports").insert({
            'issue_type': issue_type,
            'custom_issue': custom_issue if issue_type == 'custom' else None,
            'description': description,
            'location_name': location_name,
            'latitude': float(location_lat) if location_lat else None,
            'longitude': float(location_lng) if location_lng else None,
            'image_filename': image_filename
        }).execute()
        
        # Check if insertion was successful
        if data:
            return jsonify({
                'success': True,
                'message': 'Report submitted successfully',
                'report_id': data[1]['id']
            }), 201
        else:
            raise Exception("Supabase insertion failed.")

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error submitting report: {str(e)}'
        }), 500

@app.route('/api/reports', methods=['GET'])
def get_reports():
    """Get all reports with optional filtering"""
    try:
        reports = load_reports()
        
        # Optional filtering by status
        status_filter = request.args.get('status')
        if status_filter:
            reports = [r for r in reports if r.get('status') == status_filter]
        
        # Sort by timestamp (newest first)
        reports.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'success': True,
            'reports': reports,
            'total': len(reports)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error fetching reports: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>', methods=['GET'])
def get_report(report_id):
    """Get a specific report by ID"""
    try:
        reports = load_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        return jsonify({
            'success': True,
            'report': report
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error fetching report: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/sightings', methods=['POST'])
def add_sighting(report_id):
    """Add a sighting click for a report"""
    try:
        client_ip = get_client_ip()
        reports = load_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        # Initialize sightings structure if it doesn't exist (for backward compatibility)
        if 'sightings' not in report:
            report['sightings'] = {'count': 0, 'user_ips': []}
        
        # Check if user has already clicked
        if client_ip in report['sightings']['user_ips']:
            return jsonify({
                'success': False,
                'message': 'You have already reported seeing this issue'
            }), 400
        
        # Add the click
        report['sightings']['count'] += 1
        report['sightings']['user_ips'].append(client_ip)
        report['updated_at'] = datetime.now().isoformat()
        
        save_reports(reports)
        
        return jsonify({
            'success': True,
            'message': 'Sighting recorded successfully',
            'sightings_count': report['sightings']['count']
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error recording sighting: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/resolved', methods=['POST'])
def add_resolved(report_id):
    """Add a resolved click for a report"""
    try:
        client_ip = get_client_ip()
        reports = load_reports()
        report_index = next((i for i, r in enumerate(reports) if r['id'] == report_id), None)
        
        if report_index is None:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        report = reports[report_index]
        
        # Initialize resolved structure if it doesn't exist (for backward compatibility)
        if 'resolved' not in report:
            report['resolved'] = {'count': 0, 'user_ips': []}
        
        # Check if user has already clicked
        if client_ip in report['resolved']['user_ips']:
            return jsonify({
                'success': False,
                'message': 'You have already marked this issue as resolved'
            }), 400
        
        # Add the click
        report['resolved']['count'] += 1
        report['resolved']['user_ips'].append(client_ip)
        report['updated_at'] = datetime.now().isoformat()
        
        # Check if resolved count reached 10 - delete the report
        if report['resolved']['count'] >= 10:
            # Delete associated image file from Supabase Storage
            image_filename = report.get('image_filename')
            if image_filename:
                try:
                    supabase.storage.from_("reports-images").remove([f"images/{image_filename}"])
                except Exception as e:
                    print(f"Error deleting image from Supabase: {e}")
            
            # Remove report from list
            reports.pop(report_index)
            save_reports(reports)
            
            return jsonify({
                'success': True,
                'message': 'Report marked as resolved and removed (10 confirmations reached)',
                'report_deleted': True
            })
        else:
            save_reports(reports)
            return jsonify({
                'success': True,
                'message': 'Resolution vote recorded successfully',
                'resolved_count': report['resolved']['count']
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error recording resolution: {str(e)}'
        }), 500

# For already clicked verification
@app.route('/api/reports/<report_id>/user-status', methods=['GET'])
def get_user_report_status(report_id):
    """Check if current user has already clicked buttons for this report"""
    try:
        client_ip = get_client_ip()
        reports = load_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        # Initialize structures if they don't exist (backward compatibility)
        if 'sightings' not in report:
            report['sightings'] = {'count': 0, 'user_ips': []}
        if 'resolved' not in report:
            report['resolved'] = {'count': 0, 'user_ips': []}
        
        has_sighting_click = client_ip in report['sightings']['user_ips']
        has_resolved_click = client_ip in report['resolved']['user_ips']
        
        return jsonify({
            'success': True,
            'has_sighting_click': has_sighting_click,
            'has_resolved_click': has_resolved_click
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error checking user status: {str(e)}'
        }), 500
    
@app.route('/api/reports/<report_id>', methods=['PUT'])
def update_report_status(report_id):
    """Update report status"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['pending', 'in_progress', 'resolved']:
            return jsonify({
                'success': False,
                'message': 'Invalid status'
            }), 400
        
        reports = load_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        report['status'] = new_status
        report['updated_at'] = datetime.now().isoformat()
        save_reports(reports)
        
        return jsonify({
            'success': True,
            'message': 'Report status updated successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error updating report: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>', methods=['DELETE'])
def delete_report(report_id):
    """Delete a report"""
    try:
        reports = load_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            return jsonify({
                'success': False,
                'message': 'Report not found'
            }), 404
        
        # Delete associated image file from Supabase Storage
        image_filename = report.get('image_filename')
        if image_filename:
            try:
                supabase.storage.from_("reports-images").remove([f"images/{image_filename}"])
            except Exception as e:
                print(f"Error deleting image from Supabase: {e}")
        
        # Remove report from list
        reports = [r for r in reports if r['id'] != report_id]
        save_reports(reports)
        
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