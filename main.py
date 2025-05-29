from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from fpdf import FPDF
import os
import io
import json



app = Flask(__name__)
app.secret_key = 'secret_key_1234'


basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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


def load_incidents():
    with open(INCIDENTS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_incident_steps():
    with open(STEPS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.before_request
def create_tables():
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
        selected_type = request.form.get('type')

        if selected_class and selected_type:
            session['class'] = selected_class
            session['type'] = selected_type
            session['start'] = datetime.now()
            return redirect(url_for('steps', class_=selected_class, type_=selected_type))

    return render_template('incident.html', incidents=incidents, class_=selected_class, type=selected_type, show_user_dropdown=True)


@app.route('/incident/steps', methods=['GET', 'POST'])
def steps():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incident_steps = load_incident_steps()

    if isinstance(incident_steps, list) and len(incident_steps) == 1 and isinstance(incident_steps[0], list):
        incident_steps = incident_steps[0]

    if request.method == 'GET':
        class_req = request.args.get('class_')
        type_req = request.args.get('type_')
    else:
        json_data = request.get_json(silent=True) or {}
        class_req = request.form.get('class') or json_data.get('class')
        type_req = request.form.get('type') or json_data.get('type')

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

    session['class'] = class_req
    session['type'] = type_req
    session['steps'] = found_steps
    session['start'] = session.get('start') or datetime.now()
    session.modified = True

    return render_template('steps.html', steps=found_steps, class_=class_req, type_=type_req, show_user_dropdown=True)

    session['incident_class'] = class_req
    session['incident_type'] = type_req
    session['steps'] = found_steps
    session['start'] = session.get('start') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    session.modified = True

    return render_template('steps.html', steps=found_steps, class_=class_req, type_=type_req, show_user_dropdown=True)


@app.route('/incident/save_step', methods=['POST'])
def save_step():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    step_index = str(data.get('step'))
    evidence = data.get('evidence', '').strip()
    checked_substeps = data.get('checked_substeps', [])

    if not evidence:
        return jsonify({'error': 'Empty evidence'}), 400

    if 'evidences' not in session:
        session['evidences'] = {}
    session['evidences'][step_index] = evidence

    if 'substeps' not in session:
        session['substeps'] = {}
    session['substeps'][step_index] = checked_substeps

    session.modified = True
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

    if not start_time:
        start_time = datetime.now()

    if isinstance(start_time, str):
        try:
            start_time = datetime.strptime(start_time, '%d/%m/%Y %H:%M:%S')
        except:
            start_time = datetime.now()

    session['end'] = datetime.now()
    session['lessons'] = {'improvements': improvements, 'observations': observations}
    session.modified = True

    incident_class = session.get('class', 'N/A')
    incident_type = session.get('type', 'N/A')

    new_incident = Incident(
        name=f"{incident_class} - {incident_type}",
        creation_date=datetime.now(),
        status="Completed",
        improvements=improvements,
        observations=observations,
        start_datetime=start_time
    )
    db.session.add(new_incident)
    db.session.commit()

    session['id'] = new_incident.id

    session['incident_submitted'] = True

    session.modified = True


    session['report_data'] = {
        'class': incident_class,
        'type': incident_type,
        'steps': session.get('steps', []),
        'evidences': session.get('evidences', {}),
    }


    return '', 204


@app.route('/incident/complete', methods=['GET', 'POST'])
def complete():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incident_id = session.get('id')
    if not incident_id:
        incident_id = '1'

    return render_template('complete.html', show_user_dropdown=True, incident_id=incident_id)


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, 'Este relatório foi gerado automaticamente por esta aplicação', align='L')
        self.set_y(-15)
        self.set_x(-40)
        self.cell(0, 10, f'Página {self.page_no()} de {{nb}}', align='R')

@app.route('/download_report')
def download_report():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    data = session.get('report_data')
    if not data:
        return redirect(url_for('dashboard'))

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Registrar a fonte Oktah
    # font_path = os.path.join('static', 'fonts', 'Fontspring-DEMO-oktah_regular-BF651105f8625b4.ttf')
    # pdf.add_font('Arial', '', font_path, uni=True)
    # pdf.set_font("Arial", '', 12)

    # Título
    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 10, "Incident Report", ln=True, align="C")
    pdf.ln(10)

    # Informações do incidente
    pdf.set_font("Arial", '', 12)
    pdf.cell(0, 10, f"Class of Incident: {data.get('class', 'N/A')}", ln=True)
    pdf.cell(0, 10, f"Type of Incident: {data.get('type', 'N/A')}", ln=True)

    # Datas
    start_dt = session.get('start')
    start_str = datetime.strptime(start_dt, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M:%S') if isinstance(start_dt, str) else str(start_dt)
    pdf.cell(0, 10, f"Início: {start_str}", ln=True)

    end_dt = session.get('end')
    end_str = datetime.strptime(end_dt, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M:%S') if isinstance(end_dt, str) else str(end_dt)
    pdf.cell(0, 10, f"Término: {end_str}", ln=True)

    pdf.ln(10)

    # Etapas executadas
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Executed Steps", ln=True)
    pdf.ln(5)

    for idx, step in enumerate(data.get('steps', [])):
        evidences = data.get('evidences', {})
        evidence_text = evidences.get(str(idx), '')
        evidence_lines = evidence_text.split('\n')
        checked = substeps.get(str(idx), [])
        if checked:
            pdf.set_font("Arial", 'I', 11)
            pdf.multi_cell(0, 8, f"✔️ Sub-steps completed: {', '.join(checked)}")

        pdf.set_font("Arial", 'B', 12)
        pdf.multi_cell(0, 10, f"Step {idx + 1}: {step}")
        pdf.set_font("Arial", '', 12)
        for line in evidence_lines:
            pdf.multi_cell(0, 8, f"  Evidence: {line}")
        pdf.ln(3)

    pdf.ln(10)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Lessons Learned", ln=True)
    pdf.ln(5)
    pdf.set_font("Arial", '', 12)
    lessons = session.get('lessons', {})
    improvements = lessons.get('improvements', '')
    observations = lessons.get('observations', '')
    pdf.multi_cell(0, 10, f"Improvements:\n{improvements}")
    pdf.ln(5)
    pdf.multi_cell(0, 10, f"Observations:\n{observations}")

    current_date = datetime.now().strftime('%d-%m-%Y')
    incident_type = data.get('type', 'incident').replace(' ', '_').lower()
    filename = f"report_{current_date}_{incident_type}.pdf"

    pdf_output = pdf.output(dest='S').encode('latin1')
    pdf_buffer = io.BytesIO(pdf_output)
    pdf_buffer.seek(0)

    return send_file(pdf_buffer, download_name=filename, as_attachment=True)


@app.before_request
def make_session_permanent():
    session.permanent = True


if __name__ == '__main__':
    app.run(debug=True)
