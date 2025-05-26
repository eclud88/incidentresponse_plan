from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from fpdf import FPDF
import os
import io
import json

app = Flask(__name__)
app.secret_key = 'secret_key_1234'

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

USERS = {
    'admin': 'senha123',
    'user': '123456'
}

INCIDENTS_PATH = os.path.join(basedir, 'incidentes.json')
STEPS_PATH = os.path.join(basedir, 'passos_incidentes.json')


class Incident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='In Progress')
    improvements = db.Column(db.Text)
    observations = db.Column(db.Text)


def load_incidents():
    with open(INCIDENTS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_incident_steps():
    with open(STEPS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.route('/')
def index():
    current_date = datetime.now().strftime('%d/%m/%Y')
    version = "1.0.0"
    return render_template('index.html', current_date=current_date, version=version, show_user_dropdown=False)


@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']

    if username in USERS and USERS[username] == password:
        session['username'] = username
        return redirect(url_for('dashboard'))
    return redirect(url_for('index'))


@app.route('/dashboard', methods=['GET'])
def dashboard():
    username = session.get('username')
    incidents = Incident.query.order_by(Incident.creation_date.desc()).all()
    return render_template('dashboard.html', incidents=incidents, show_user_dropdown=True)


@app.route('/incident', methods=['GET', 'POST'])
def incident():
    incidents = load_incidents()
    incident_steps = load_incident_steps()
    if isinstance(incident_steps[0], list):
        incident_steps = incident_steps[0]

    steps = []
    selected_class = ''
    selected_type = ''

    if request.method == 'POST':
        selected_class = request.form.get('class')
        selected_type = request.form.get('type')

        session['class'] = selected_class
        session['type'] = selected_type
        session['start'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        if selected_class and selected_type:
            return redirect(url_for('steps', selected_class=selected_class, selected_type=selected_type))

    return render_template('incident.html', incidents=incidents, steps=steps, selected_class=selected_class, selected_type=selected_type, show_user_dropdown=True)


@app.route('/incident/steps', methods=['GET', 'POST'])
def steps():
    session['record_start'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    incident_steps = load_incident_steps()

    if isinstance(incident_steps, list) and len(incident_steps) == 1 and isinstance(incident_steps[0], list):
        incident_steps = incident_steps[0]

    if request.method == 'GET':
        class_req = request.args.get('class')
        type_req = request.args.get('type')
    else:
        class_req = request.form.get('class') or (request.get_json() or {}).get('class')
        type_req = request.form.get('type') or (request.get_json() or {}).get('type')

    if not class_req or not type_req:
        return abort(400, description="Parameters 'class' and 'type' are required.")

    found_steps = None
    for item in incident_steps:
        if item.get('class', '').lower() == class_req.lower():
            for type_item in item.get('types', []):
                if type_item.get('type', '').lower() == type_req.lower():
                    found_steps = type_item.get('steps', [])
                    break
            if found_steps:
                break

    if not found_steps:
        return abort(404, description="Step plan not found.")

    session['class'] = class_req
    session['type'] = type_req
    session['steps'] = found_steps

    return render_template('steps.html', steps=found_steps, selected_class=class_req, selected_type=type_req, show_user_dropdown=True)


@app.route('/incident/save_step', methods=['POST'])
def save_step():
    data = request.get_json()
    step_index = str(data.get('step'))
    evidence = data.get('evidence', '').strip()

    if not evidence:
        return jsonify({'error': 'Empty evidence'}), 400

    if 'evidences' not in session:
        session['evidences'] = {}

    session['evidences'][step_index] = evidence
    session.modified = True
    return {'status': 'ok'}


@app.route('/save_completion', methods=['POST'])
def save_completion():
    if not request.is_json:
        return {'error': 'JSON format expected'}, 400

    data = request.get_json()
    evidence = data.get('evidence', '').strip()
    step = str(data.get('step'))

    improvements = data.get('improvements', '').strip()
    observations = data.get('observations', '').strip()

    if not improvements or not observations:
        return {'error': 'All fields must be filled'}, 400

    if 'evidences' not in session:
        session['evidences'] = {}

    session['evidences'][step] = evidence
    session['lessons'] = {'improvements': improvements, 'observations': observations}
    session['end'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    session.modified = True
    return '', 204


@app.route('/incident/complete', methods=['GET', 'POST'])
def complete():
    return render_template('complete.html')


@app.route('/incident/report')
def report():
    incident_class = session.get('class', 'N/A')
    incident_type = session.get('type', 'N/A')
    steps = session.get('steps', [])
    evidences = session.get('evidences', {})
    record_start = session.get('record_start', 'N/A')
    end_time = session.get('end', datetime.now().strftime('%d/%m/%Y %H:%M:%S'))
    lessons = session.get('lessons', {})

    improvements = lessons.get('improvements', '')
    observations = lessons.get('observations', '')

    new_incident = Incident(
        name=f"{incident_class} - {incident_type}",
        creation_date=datetime.now(),
        status="Completed",
        improvements=improvements,
        observations=observations
    )
    db.session.add(new_incident)
    db.session.commit()

    session['report_ready'] = True
    session['report_data'] = {
        'class': incident_class,
        'type': incident_type,
        'steps': steps,
        'evidences': evidences,
        'record_start': record_start,
        'end': end_time,
        'improvements': improvements,
        'observations': observations
    }

    return redirect(url_for('dashboard'))


@app.route('/download_report')
def download_report():
    data = session.get('report_data')
    if not data:
        return redirect(url_for('dashboard'))

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 10, "Incident Report", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Arial", '', 12)
    pdf.cell(0, 10, f"Incident Class: {data['class']}", ln=True)
    pdf.cell(0, 10, f"Incident Type: {data['type']}", ln=True)
    pdf.cell(0, 10, f"Start Time: {data['record_start']}", ln=True)
    pdf.cell(0, 10, f"End Time: {data['end']}", ln=True)
    pdf.ln(10)

    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Executed Steps:", ln=True)
    pdf.ln(5)

    for idx, step in enumerate(data['steps']):
        evidence_lines = data['evidences'].get(str(idx), '').split('\n')
        pdf.set_font("Arial", 'B', 12)
        pdf.multi_cell(0, 10, f"Step {idx + 1}: {step}")
        pdf.set_font("Arial", '', 12)
        for line in evidence_lines:
            pdf.cell(10)
            pdf.cell(0, 10, f"- {line.strip()}", ln=True)
        pdf.ln(3)

    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Lessons Learned:", ln=True)
    pdf.set_font("Arial", '', 12)
    pdf.multi_cell(0, 10, f"Improvements: {data['improvements']}")
    pdf.multi_cell(0, 10, f"Observations: {data['observations']}")
    pdf.ln(10)

    pdf.set_y(-30)
    pdf.set_font("Arial", 'I', 10)
    today = datetime.now().strftime('%d/%m/%Y')
    pdf.cell(0, 10, f"This report was generated automatically on {today}.", align="L")
    pdf.cell(0, 10, f"Page {pdf.page_no()}", align="R")

    pdf_bytes = pdf.output(dest='S').encode('latin-1')
    pdf_stream = io.BytesIO(pdf_bytes)
    now_str = datetime.now().strftime("%d-%m-%Y_%H%M%S")
    filename = f"report_{now_str}_{data['type']}.pdf"

    session.pop('report_ready', None)
    session.pop('report_data', None)

    return send_file(pdf_stream, mimetype='application/pdf', download_name=filename, as_attachment=True)


@app.before_request
def create_tables():
    db.create_all()


@app.route('/delete_incident/<int:id>', methods=['POST'])
def delete_incident(id):
    try:
        incident = Incident.query.get_or_404(id)
        db.session.delete(incident)
        db.session.commit()
        return '', 200
    except:
        return jsonify({'error': 'Failed to delete'}), 400


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
