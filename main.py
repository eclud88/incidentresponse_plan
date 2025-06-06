from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from fpdf import FPDF
import os
import io
import json


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-dev-key')


basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)


db = SQLAlchemy(app)


USERS = {
    'admin': 'senha123',
    'user': '123456'
}


INCIDENTS_PATH = os.path.join(basedir, 'incidents.json')
STEPS_PATH = os.path.join(basedir, 'incident_steps.json')


class Incident(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100))
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='In Progress')
    improvements = db.Column(db.Text)
    observations = db.Column(db.Text)
    start_datetime = db.Column(db.DateTime, nullable=True)
    end_datetime = db.Column(db.DateTime, nullable=True)


def load_incidents():
    with open(INCIDENTS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_incident_steps():
    with open(STEPS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


with app.app_context():
    db.create_all()


@app.route('/delete_incident/<int:id>', methods=['POST'])
def delete_incident(id):
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    incident = Incident.query.get(id)
    if not incident:
        return jsonify({'error': 'Incident not found'}), 404

    try:
        db.session.delete(incident)
        db.session.commit()
        return jsonify({'status': 'Deleted'})
    except Exception as e:
        return jsonify({'error': 'Failed to delete incident'}), 500


@app.route('/')
def index():
    current_date = datetime.now().strftime('%d/%m/%Y')
    version = "1.0.0"
    return render_template('index.html', current_date=current_date, version=version, show_user_dropdown=False)


@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if username in USERS and USERS[username] == password:
        session['username'] = username
        return redirect(url_for('dashboard'))
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incidents = Incident.query.order_by(Incident.creation_date.desc()).all()
    return render_template('dashboard.html', incidents=incidents, show_user_dropdown=True, username=username)


@app.route('/incident', methods=['GET', 'POST'])
def incident():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incidents = load_incidents()
    selected_class = ''
    selected_type = ''

    if request.method == 'POST':
        selected_class = request.form.get('class')
        print('SELECTED CLASS: ', selected_class)
        selected_type = request.form.get('type')

        if selected_class and selected_type:
            session['class_'] = selected_class
            session['type_'] = selected_type
            session['start'] = datetime.now()

            return redirect(url_for('steps', class_=selected_class, type_=selected_type))

    return render_template(
        'incident.html',
        incidents=incidents,
        show_user_dropdown=True
    )


@app.route('/incident/steps', methods=['GET', 'POST'])
def steps():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incident_id = session.get('incident_id') or '1'  # or however you define it
    session['incident_id'] = incident_id

    incident_steps = load_incident_steps()
    if len(incident_steps) == 1 and isinstance(incident_steps[0], list):
        incident_steps = incident_steps[0]

    if request.method == 'GET':
        class_param = request.args.get('class_')
        type_param = request.args.get('type_')

        checked_substeps = request.args.get('checked_substeps')
    else:
        json_data = request.get_json(silent=True) or {}
        class_param = (
            request.form.get('class') or request.form.get('class_') or
            json_data.get('class') or json_data.get('class_') or
            request.args.get('class') or request.args.get('class_')
        )
        type_param = (
            request.form.get('type') or request.form.get('type_') or
            json_data.get('type') or json_data.get('type_') or
            request.args.get('type') or request.args.get('type_')
        )

        checked_substeps = (
            request.form.get('checked_substeps') or
            request.args.get('checked_substeps') or
            json_data.get('checked_substeps')
        )

    if not class_param or not type_param:
        return abort(400, description="Parameters 'class' and 'type' are required.")

    # Match steps
    found_steps = []
    for item in incident_steps:
        if item.get('class', '').strip().lower() == class_param.strip().lower():
            for type_item in item.get('types', []):
                if type_item.get('type', '').strip().lower() == type_param.strip().lower():
                    found_steps = type_item.get('steps', [])
                    break

    # Store in session
    session['class'] = class_param
    session['type'] = type_param
    session['steps'] = found_steps
    session['checked_substeps'] = checked_substeps
    session['start'] = session.get('start') or datetime.now().isoformat()
    session.modified = True

    return render_template('steps.html', incident_id=incident_id, checked_substeps=checked_substeps, steps=found_steps, class_=class_param, type_=type_param, show_user_dropdown=True)


@app.route('/incident/save_step', methods=['POST'])
def save_step():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()

    try:
        step_index = str(int(data.get('step', -1)))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid step index'}), 400

    evidence = data.get('evidence', '').strip()
    if not evidence:
        return jsonify({'error': 'Empty evidence'}), 400

    checked_substeps = data.get('checked_substeps', [])
    if not isinstance(checked_substeps, list):
        return jsonify({'error': 'Invalid substeps format'}), 400



    # Store evidence and sub-steps in top-level session keys
    evidences = session.setdefault('evidences', {})
    sub_steps = session.setdefault('sub_steps', {})

    evidences[step_index] = evidence
    sub_steps[step_index] = checked_substeps

    session.modified = True

    print(f"Saved step {step_index}:")
    print(f"- Evidence: {evidence}")
    print(f"- Sub-steps: {checked_substeps}")

    return jsonify({'status': 'ok'})


@app.route('/save_completion', methods=['POST'])
def save_completion():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    if not request.is_json:
        return jsonify({'error': 'JSON format expected'}), 400

    data = request.get_json()
    improvements = data.get('improvements', '').strip()
    observations = data.get('observations', '').strip()

    if not improvements or not observations:
        return jsonify({'error': 'All fields must be filled'}), 400

    start_time = session.get('start')
    if isinstance(start_time, str):
        try:
            start_time = datetime.fromisoformat(start_time)
        except ValueError:
            start_time = datetime.now()
    elif not start_time:
        start_time = datetime.now()

    end_time = session.get('end', datetime.now())
    if isinstance(end_time, str):
        try:
            end_time = datetime.fromisoformat(end_time)
        except ValueError:
            end_time = datetime.now()

    session['end'] = end_time
    session['lessons'] = {'improvements': improvements, 'observations': observations}


    incident_class = session.get('class', 'N/A')
    incident_type = session.get('type', 'N/A')

    try:
        new_incident = Incident(
            name=f"{incident_class} - {incident_type}",
            creation_date=datetime.now(),
            status="Completed",
            improvements=improvements,
            observations=observations,
            start_datetime=start_time,
            end_datetime=end_time
        )
        db.session.add(new_incident)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    session['id'] = new_incident.id
    session['incident_submitted'] = True

    session['report_data'] = {
        'class': incident_class,
        'type': incident_type,
        'steps': session.get('steps', []),
        'sub_steps': session.get('sub_steps', {}),
        'evidences': session.get('evidences', {}),
        'attachments': session.get('attachments', {})
    }

    session.modified = True

    return jsonify({'status': 'success'}), 200



@app.route('/incident/complete', methods=['GET', 'POST'])
def complete():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incident_id = session.get('incident_id', '1')
    print('incident ID:' , incident_id)

    return render_template('complete.html', show_user_dropdown=True, incident_id=incident_id)


ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/incident/upload_file', methods=['POST'])
def upload_file():
    step_index = request.form.get('step')
    incident_id = session.get('incident_id', '1')
    file = request.files.get('file')

    if not step_index or not incident_id or not file:
        return jsonify({
            'status': 'fail',
            'message': f'Missing data. step={step_index}, incident_id={incident_id}, file_present={bool(file)}'
        })

    if not allowed_file(file.filename):
        return jsonify({
            'status': 'fail',
            'message': f'File type not allowed: {file.filename}'
        })

    try:
        filename = secure_filename(file.filename)
        upload_folder = os.path.join(app.root_path, 'uploads', step_index, str(incident_id))
        os.makedirs(upload_folder, exist_ok=True)

        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)
        rel_path = os.path.relpath(file_path, app.root_path)

        report_data = session.get('report_data', {})
        attachments = report_data.setdefault('attachments', {})
        incident_attachments = attachments.setdefault(str(incident_id), {})
        step_files = incident_attachments.setdefault(str(step_index), [])

        if rel_path not in step_files:
            step_files.append(rel_path)

        session['report_data'] = report_data
        session.modified = True

        return jsonify({'status': 'success', 'filepath': rel_path})
    except Exception as e:
        return jsonify({'status': 'fail', 'message': f'Error: {str(e)}'})




class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Oktah', 'I', 8)
        self.cell(0, 10, 'This report was automatically generated by this application', align='L')
        self.set_y(-15)
        self.set_x(-40)
        self.cell(0, 10, f'Page {self.page_no()} of {{nb}}', align='R')


@app.route('/download_report')
def download_report():
    font_path = "static/fonts/Fontspring-DEMO-oktah_regular-BF651105f8625b4.ttf"

    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    data = session.get('report_data')
    if not data:
        return redirect(url_for('dashboard'))

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Register font
    pdf.add_font("Oktah", "", font_path)
    pdf.add_font("Oktah", "B", font_path)
    pdf.add_font("Oktah", "I", font_path)
    pdf.add_font("Oktah", "BI", font_path)

    pdf.add_page()
    pdf.set_font("Oktah", 'B', 18)
    pdf.cell(0, 10, "Incident Report", ln=True, align="C")
    pdf.ln(10)

    # Incident Class and Type
    pdf.set_font("Oktah", '', 12)
    incident_class = data.get('class', 'N/A')
    incident_type = data.get('type', 'N/A')
    pdf.multi_cell(0, 10, f"Incident Class: {incident_class}\nIncident Type: {incident_type}")
    pdf.ln(3)

    # Dates
    start_dt = session.get('start')
    end_dt = session.get('end')

    if isinstance(start_dt, str):
        try:
            start_dt = datetime.fromisoformat(start_dt)
        except ValueError:
            start_dt = datetime.now()

    if isinstance(end_dt, str):
        try:
            end_dt = datetime.strptime(end_dt, '%d-%m-%Y %H:%M:%S')
        except ValueError:
            end_dt = datetime.now()

    pdf.cell(0, 10, f"Start: {start_dt.strftime('%d/%m/%Y %H:%M:%S')}", ln=True)
    pdf.cell(0, 10, f"End: {end_dt.strftime('%d/%m/%Y %H:%M:%S')}", ln=True)
    pdf.ln(10)

    # Steps Section
    pdf.set_font("Oktah", 'B', 14)
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(0, 10, "Executed Steps", ln=True, fill=True)
    pdf.ln(5)

    steps = data.get('steps', [])
    evidences = data.get('evidences', {})
    substeps = data.get('sub_steps', {})
    attachments = data.get('attachments', {})

    for idx, step in enumerate(steps):
        step_text = step.get('step') if isinstance(step, dict) else str(step)
        substep_list = step.get('sub_steps', []) if isinstance(step, dict) else []

        # Step header
        pdf.set_font("Oktah", 'B', 12)
        pdf.set_fill_color(240, 248, 255)
        pdf.multi_cell(0, 8, f"Step {idx + 1}: {step_text}", fill=True)
        pdf.ln(5)

        # Substeps
        checked = substeps.get(str(idx), [])
        if substep_list:
            pdf.set_font("Oktah", '', 11)
            for sub in substep_list:
                symbol = "o" if sub in checked else "X"
                color = (0, 128, 0) if sub in checked else (180, 0, 0)
                pdf.set_text_color(*color)
                pdf.multi_cell(0, 6, f"{symbol} {sub}")
                pdf.ln(1)
            pdf.set_text_color(0, 0, 0)

        pdf.ln(5)

        # Evidence text
        evidence_text = evidences.get(str(idx), '')
        if evidence_text:
            pdf.set_font("Oktah", '', 11)
            pdf.set_text_color(0, 0, 80)
            pdf.multi_cell(0, 6, f"📝 Evidence:\n{evidence_text}")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(5)

        # Image attachments
        # attached_files = attachments.get('uploads', {}).get(str(idx), [])
        uploads_root = 'uploads'
        incident_id = session.get('incident_id', '1')
        folder_path = os.path.join(uploads_root, str(idx), str(incident_id))

        print('folder_path: ', folder_path)

        if os.path.isdir(folder_path):
            for file in os.listdir(folder_path):
                print('File:', file)
                full_path = os.path.join(folder_path, file)

                try:
                    pdf.image(full_path, w=100)
                    pdf.ln(5)
                except Exception as e:
                    pdf.set_text_color(255, 0, 0)
                    pdf.cell(0, 10, f"⚠️ Error loading image: {file}", ln=True)
                    pdf.set_text_color(0, 0, 0)

    # Lessons Learned
    pdf.set_font("Oktah", 'B', 14)
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(0, 10, "Lessons Learned", fill=True)
    pdf.ln(5)
    pdf.set_font("Oktah", '', 12)

    lessons = session.get('lessons', {})
    improvements = lessons.get('improvements', '')
    observations = lessons.get('observations', '')

    pdf.ln(5)
    pdf.multi_cell(0, 8, f"Improvements:\n{improvements}")
    pdf.ln(3)
    pdf.multi_cell(0, 8, f"Observations:\n{observations}")

    # Export PDF
    pdf_buffer = io.BytesIO()
    pdf.output(pdf_buffer)
    pdf_buffer.seek(0)


    current_date = datetime.now().strftime('%Y-%m-%d')
    incident_type_slug = incident_type.replace(' ', '_').lower()
    filename = f"report_{current_date}_{incident_type_slug}.pdf"

    return send_file(pdf_buffer, download_name=filename, as_attachment=True)



@app.before_request
def make_session_permanent():
    session.permanent = True


@app.route('/.well-known/appspecific/com.chrome.devtools.json', methods=['GET'])
def devtools_json():
    return jsonify({"status": "ok"}), 200


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/favicon.ico')
def favicon():
    return '', 204



if __name__ == '__main__':
    app.run(debug=True)
