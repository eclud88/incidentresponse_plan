from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from fpdf import FPDF
from docxtpl import DocxTemplate
import tempfile
import platform
import os
import io
import json
import subprocess


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



@app.route('/incident/<int:incident_id>/second_download')
def second_download_report(incident_id):
    pdf_path = os.path.join('reports', f'incident_{incident_id}.pdf')

    if os.path.exists(pdf_path):
        return send_file(pdf_path, as_attachment=True)
    else:
        abort(404, description="Report not found.")


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
    version = "1.0"
    return render_template('index.html', current_date=current_date, version=version, show_user_dropdown=False)


@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if username in USERS and USERS[username] == password:
        session['username'] = username
        return redirect(url_for('dashboard'))
    message = 'Incorrect user or password!'
    return render_template('index.html', message=message)


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

    return render_template('steps.html', checked_substeps=checked_substeps, steps=found_steps, class_=class_param, type_=type_param, show_user_dropdown=True)


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



@app.route('/incident/upload_file', methods=['POST'])
def upload_file():
    step_index = str(int(request.form['step']) + 1)
    file = request.files['file']
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}

    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        incident_id = session.get('incident_id', '1')
        upload_folder = os.path.join('uploads', incident_id, step_index)
        os.makedirs(upload_folder, exist_ok=True)
        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)

        # Store file path in session
        if 'report_data' not in session:
            session['report_data'] = {}
        if 'attachments' not in session['report_data']:
            session['report_data']['attachments'] = {}
        step_index_str = str(step_index)
        if step_index_str not in session['report_data']['attachments']:
            session['report_data']['attachments'][step_index_str] = []
        session['report_data']['attachments'][step_index_str].append(file_path)

        session.modified = True

        return jsonify({'status': 'success'})
    return jsonify({'status': 'fail'})






@app.route('/download_report')
def download_report():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    def format_steps(steps):
        return '\n'.join(f"{i + 1}. {step['step']}" for i, step in enumerate(steps))

    def format_dict(d):
        if isinstance(d, dict):
            return '\n'.join(f"{k}: {v}" for k, v in d.items())
        return str(d)

    data = session.get('report_data')
    if not data:
        return redirect(url_for('dashboard'))

    lessons = session.get('lessons', {})
    start_dt = session.get('start')
    end_dt = session.get('end')

    def parse_dt(dt):
        if isinstance(dt, str):
            try:
                return datetime.fromisoformat(dt)
            except Exception:
                return datetime.now()
        return dt if isinstance(dt, datetime) else datetime.now()

    start_dt = parse_dt(start_dt)
    end_dt = parse_dt(end_dt)

    dados_template = {
        'incident_id': session.get('id', 'N/A'),
        'selected_class': data.get('class', 'N/A'),
        'selected_type': data.get('type', 'N/A'),
        'start_time': start_dt.strftime('%d/%m/%Y %H:%M:%S'),
        'end_time': end_dt.strftime('%d/%m/%Y %H:%M:%S'),
        'steps': data.get('steps', []),
        'substeps': format_dict(data.get('sub_steps', {})),
        'evidences': format_dict(data.get('evidences', {})),
        'attachments': format_dict(data.get('attachments', [])),
        'improvements': lessons.get('improvements', ''),
        'observations': lessons.get('observations', '')
    }

    basedir = os.path.abspath(os.path.dirname(__file__))
    template_path = os.path.join(basedir, 'word_templates', 'incidentreport_template.docx')

    pdf_path = gerar_docx_com_dados(dados_template, template_path=template_path)
    filename = f"report_{datetime.now().strftime('%d-%m-%Y')}_{dados_template['selected_type'].replace(' ', '_')}.pdf"
    return send_file(pdf_path, as_attachment=True, download_name=filename)


def flatten_data(data):
    """Função recursiva que transforma objetos aninhados em texto para pesquisa."""
    if isinstance(data, dict):
        return ' '.join(flatten_data(v) for v in data.values())
    elif isinstance(data, list):
        return ' '.join(flatten_data(item) for item in data)
    else:
        return str(data)

@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    query = data.get('query', '').lower()

    incidents = load_incidents()
    steps_data = load_incident_steps()

    results = []

    for incident in incidents:
        class_name = incident.get('class', '')
        if query in class_name.lower():
            results.append({
                "class": class_name
            })

        for type_obj in incident.get('types', []):
            type_name = type_obj.get('type', '')
            if query in type_name.lower():
                results.append({
                    "class": class_name,
                    "type": type_name
                })


    for entry in steps_data:
        class_name = entry.get('class', '')
        for type_obj in entry.get('types', []):
            type_name = type_obj.get('type', '')
            for step in type_obj.get('steps', []):
                step_text = step.get('step', '')
                if query in step_text.lower():
                    results.append({
                        "class": class_name,
                        "type": type_name,
                        "step": step_text
                    })
                for sub in step.get('sub_steps', []):
                    if query in sub.lower():
                        results.append({
                            "class": class_name,
                            "type": type_name,
                            "substep": sub
                        })

    return jsonify({'results': results})



def gerar_docx_com_dados(dados, template_path='word_templates/incidentreport_template.docx'):
    temp_dir = tempfile.mkdtemp()
    output_docx = os.path.join(temp_dir, 'incident_report.docx')

    # Preencher o template
    doc = DocxTemplate(template_path)
    doc.render(dados)
    doc.save(output_docx)

    # Converter com LibreOffice
    output_pdf = converter_para_pdf_com_libreoffice(output_docx)

    return output_pdf  # retorna o caminho final do PDF




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



def converter_para_pdf_com_libreoffice(docx_path):
    output_dir = os.path.dirname(docx_path)
    libreoffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"

    if not os.path.exists(libreoffice_path):
        raise FileNotFoundError("LibreOffice não foi encontrado no caminho especificado.")

    try:
        subprocess.run([
            libreoffice_path, "--headless", "--convert-to", "pdf", "--outdir", output_dir, docx_path
        ], check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Erro ao converter DOCX para PDF com LibreOffice: {e}")

    pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
    return pdf_path



if __name__ == '__main__':
    app.run(debug=True)