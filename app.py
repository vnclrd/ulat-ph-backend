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

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the distance between two points on Earth
    using the Haversine formula.
    """
    R = 6371  # Radius of Earth in kilometers
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance

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
    
# API endpoint to get all reports from Supabase
# Change the function to handle both GET and POST requests
@app.route('/api/reports', methods=['GET', 'POST'])
def reports_handler():
    # Handle GET request for fetching reports
    if request.method == 'GET':
        try:
            # Get latitude and longitude from query parameters
            user_lat = request.args.get('latitude', type=float)
            user_lng = request.args.get('longitude', type=float)

            # Retrieve all reports from Supabase
            response = supabase.from_("reports").select("*").execute()
            all_reports = response.data

            # Filter reports based on distance
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

    # Handle POST request for creating a new report
    elif request.method == 'POST':
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
            response = supabase.from_("reports").insert({
                'issue_type': issue_type,
                'custom_issue': custom_issue if issue_type == 'custom' else None,
                'description': description,
                'location_name': location_name,
                'latitude': float(location_lat) if location_lat else None,
                'longitude': float(location_lng) if location_lng else None,
                'image_filename': image_filename
            }).execute()
            
            # The returned response has a 'data' key which is the list of inserted rows
            inserted_data = response.data

            # Check if insertion was successful
            if inserted_data:
                return jsonify({
                    'success': True,
                    'message': 'Report submitted successfully',
                    'report_id': inserted_data[0]['id']
                }), 201
            else:
                raise Exception("Supabase insertion failed.")

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error submitting report: {str(e)}'
            }), 500

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
        response = supabase.from_("reports").insert({
            'issue_type': issue_type,
            'custom_issue': custom_issue if issue_type == 'custom' else None,
            'description': description,
            'location_name': location_name,
            'latitude': float(location_lat) if location_lat else None,
            'longitude': float(location_lng) if location_lng else None,
            'image_filename': image_filename
        }).execute()
        
        # The returned response has a 'data' key which is the list of inserted rows
        inserted_data = response.data

        # Check if insertion was successful
        if inserted_data:
            return jsonify({
                'success': True,
                'message': 'Report submitted successfully',
                'report_id': inserted_data[0]['id']
            }), 201
        else:
            raise Exception("Supabase insertion failed.")

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error submitting report: {str(e)}'
        }), 500
    
# Placeholder functions
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
    try:
        # Get the IP address of the client
        client_ip = get_client_ip()

        # Check if the user has already recorded a sighting for this report
        existing_sighting = supabase.from_("sighting_clicks").select("*").eq("report_id", report_id).eq("client_ip", client_ip).execute().data
        if existing_sighting:
            return jsonify({
                'success': False,
                'message': "You've already recorded a sighting for this report."
            }), 409 # Conflict

        # Increment sightings count in the reports table
        reports_response = supabase.from_("reports").select("sightings_count").eq("id", report_id).single().execute()
        current_sightings = reports_response.data.get("sightings_count", 0)
        
        supabase.from_("reports").update({"sightings_count": current_sightings + 1}).eq("id", report_id).execute()

        # Record the user's sighting in the clicks table
        supabase.from_("sighting_clicks").insert({
            'report_id': report_id,
            'client_ip': client_ip
        }).execute()

        return jsonify({
            'success': True,
            'message': 'Sighting recorded successfully'
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error updating sighting: {str(e)}'
        }), 500

@app.route('/api/reports/<report_id>/resolved', methods=['POST'])
def add_resolved(report_id):
    try:
        # Get the IP address of the client
        client_ip = get_client_ip()

        # Check if the user has already recorded a resolved click for this report
        existing_resolved_click = supabase.from_("resolved_clicks").select("*").eq("report_id", report_id).eq("client_ip", client_ip).execute().data
        if existing_resolved_click:
            return jsonify({
                'success': False,
                'message': "You've already recorded this as resolved."
            }), 409

        # Increment resolved count in the reports table
        reports_response = supabase.from_("reports").select("resolved_count").eq("id", report_id).single().execute()
        current_resolved_count = reports_response.data.get("resolved_count", 0)
        
        supabase.from_("reports").update({"resolved_count": current_resolved_count + 1}).eq("id", report_id).execute()

        # Record the user's resolved click
        supabase.from_("resolved_clicks").insert({
            'report_id': report_id,
            'client_ip': client_ip
        }).execute()

        # If resolved count reaches 5, delete the report
        reports_response_after = supabase.from_("reports").select("resolved_count").eq("id", report_id).single().execute()
        current_resolved_count_after = reports_response_after.data.get("resolved_count", 0)

        if current_resolved_count_after >= 5:
            supabase.from_("reports").delete().eq("id", report_id).execute()
            return jsonify({
                'success': True,
                'message': "Report marked as resolved and deleted.",
                'report_deleted': True
            })

        return jsonify({
            'success': True,
            'message': "Resolved status recorded successfully",
            'report_deleted': False
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error updating resolved status: {str(e)}'
        }), 500

# For already clicked verification
@app.route('/api/reports/<report_id>/user-status', methods=['GET'])
def get_user_status(report_id):
    try:
        client_ip = get_client_ip()

        has_sighting_click = supabase.from_("sighting_clicks").select("*").eq("report_id", report_id).eq("client_ip", client_ip).execute().data
        has_resolved_click = supabase.from_("resolved_clicks").select("*").eq("report_id", report_id).eq("client_ip", client_ip).execute().data
        
        return jsonify({
            'success': True,
            'has_sighting_click': len(has_sighting_click) > 0,
            'has_resolved_click': len(has_resolved_click) > 0
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error fetching user status: {str(e)}'
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