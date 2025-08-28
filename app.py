from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from geopy.geocoders import Nominatim
from math import radians, sin, cos, sqrt, atan2
import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from supabase import create_client, Client

# Load Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__, static_folder="frontend/build", static_url_path="/")
CORS(app, resources={r"/*": {"origins": "*"}})  # Allow all origins for now

geolocator = Nominatim(user_agent="location_app")

# File upload config
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_client_ip():
    """Get client IP address"""
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two points on Earth"""
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.route("/")
def serve_react():
    """Serve the React frontend"""
    return send_from_directory(app.static_folder, "index.html")

# ---------------------------
# API ENDPOINTS
# ---------------------------

# Get all reports OR create a new one
@app.route("/api/reports", methods=["GET", "POST"])
def handle_reports():
    try:
        if request.method == "GET":
            # Get user's coordinates if provided
            user_lat = request.args.get("latitude", type=float)
            user_lng = request.args.get("longitude", type=float)

            response = supabase.from_("reports").select("*").execute()
            reports = response.data

            # Filter by distance <= 1km if user location is given
            if user_lat is not None and user_lng is not None:
                reports = [
                    report
                    for report in reports
                    if haversine(user_lat, user_lng, report["latitude"], report["longitude"]) <= 1
                ]

            return jsonify({"success": True, "reports": reports}), 200

        elif request.method == "POST":
            # Handle image upload
            image_filename = None
            if "image" in request.files:
                file = request.files["image"]
                if file and allowed_file(file.filename):
                    file_ext = secure_filename(file.filename).rsplit(".", 1)[1].lower()
                    image_uuid = str(uuid.uuid4())
                    image_filename = f"{image_uuid}.{file_ext}"

                    # Upload to Supabase Storage
                    supabase_upload_path = f"images/{image_filename}"
                    supabase.storage.from_("reports-images").upload(
                        supabase_upload_path,
                        file
                    )

            # Get form data
            issue_type = request.form.get("issueType", "")
            custom_issue = request.form.get("customIssue", "")
            description = request.form.get("description", "")
            location_name = request.form.get("location", "")
            location_lat = request.form.get("latitude", "")
            location_lng = request.form.get("longitude", "")

            # Insert into Supabase
            response = supabase.from_("reports").insert({
                "issue_type": issue_type,
                "custom_issue": custom_issue if issue_type == "custom" else None,
                "description": description,
                "location_name": location_name,
                "latitude": float(location_lat) if location_lat else None,
                "longitude": float(location_lng) if location_lng else None,
                "image_filename": image_filename,
                "sightings": {"count": 0, "device_ids": [], "user_ips": []},
                "resolved": {"count": 0, "device_ids": [], "user_ips": []},
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            inserted_data = response.data
            if inserted_data:
                return jsonify({
                    "success": True,
                    "message": "Report submitted successfully",
                    "report_id": inserted_data[0]["id"]
                }), 201
            else:
                raise Exception("Failed to insert report.")

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# SIGHTINGS BUTTON
# ---------------------------
@app.route("/api/reports/<report_id>/sightings", methods=["POST"])
def add_sighting(report_id):
    try:
        client_ip = get_client_ip()
        data = request.get_json()
        device_id = data.get("device_id")

        report = supabase.from_("reports").select("sightings").eq("id", report_id).single().execute().data
        sightings = report.get("sightings") or {"count": 0, "device_ids": [], "user_ips": []}

        if device_id in sightings["device_ids"] or client_ip in sightings["user_ips"]:
            return jsonify({"success": False, "message": "You've already marked this sighting."}), 409

        sightings["count"] += 1
        sightings["device_ids"].append(device_id)
        sightings["user_ips"].append(client_ip)

        supabase.from_("reports").update({"sightings": sightings}).eq("id", report_id).execute()

        return jsonify({"success": True, "message": "You've marked this as seen. Thank you!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# RESOLVED BUTTON
# ---------------------------
@app.route("/api/reports/<report_id>/resolved", methods=["POST"])
def add_resolved(report_id):
    try:
        client_ip = get_client_ip()
        data = request.get_json()
        device_id = data.get("device_id")

        report = supabase.from_("reports").select("resolved").eq("id", report_id).single().execute().data
        resolved = report.get("resolved") or {"count": 0, "device_ids": [], "user_ips": []}

        if device_id in resolved["device_ids"] or client_ip in resolved["user_ips"]:
            return jsonify({"success": False, "message": "You've already marked this as resolved."}), 409

        resolved["count"] += 1
        resolved["device_ids"].append(device_id)
        resolved["user_ips"].append(client_ip)

        supabase.from_("reports").update({"resolved": resolved}).eq("id", report_id).execute()

        # Auto-delete report after 5 resolved confirmations
        if resolved["count"] >= 5:
            supabase.from_("reports").delete().eq("id", report_id).execute()
            return jsonify({"success": True, "message": "Report deleted after multiple resolved confirmations.", "report_deleted": True})

        return jsonify({"success": True, "message": "Marked as resolved. Thank you!", "report_deleted": False})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# USER STATUS CHECK
# ---------------------------
@app.route("/api/reports/<report_id>/user-status", methods=["GET"])
def get_user_status(report_id):
    try:
        client_ip = get_client_ip()
        device_id = request.args.get("device_id")

        report = supabase.from_("reports").select("sightings, resolved").eq("id", report_id).single().execute().data

        sightings = report.get("sightings", {})
        resolved = report.get("resolved", {})

        has_sighting_click = device_id in sightings.get("device_ids", []) or client_ip in sightings.get("user_ips", [])
        has_resolved_click = device_id in resolved.get("device_ids", []) or client_ip in resolved.get("user_ips", [])

        return jsonify({
            "success": True,
            "has_sighting_click": has_sighting_click,
            "has_resolved_click": has_resolved_click
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------------------
# RUN SERVER
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
