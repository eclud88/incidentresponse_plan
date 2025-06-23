from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from fpdf import FPDF
from docxtpl import DocxTemplate, InlineImage
import tempfile
import platform
import os
import io
import json
import subprocess
from docx.shared import Inches


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-dev-key')


basedir = os.path.abspath(os.path.dirname(__file__))

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True  # True se usares HTTPS
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
db = SQLAlchemy(app)


USERS = {
    'admin': 'senha123',
    'user': '123456'
}


INCIDENTS_PATH = os.path.join(basedir, 'incidents.json')
STEPS_PATH = os.path.join(basedir, 'incident_steps.json')


class InProgressIncident(db.Model):
    __tablename__ = 'in_progress_incidents'

    id = db.Column(db.Integer, primary_key=True)
    class_param = db.Column(db.String(100))
    type_param = db.Column(db.String(100))
    start_date = db.Column(db.DateTime, default=datetime.utcnow)

    steps = db.Column(db.PickleType, default=[])
    evidences = db.Column(db.PickleType, default=[])
    observations = db.Column(db.Text)
    improvements = db.Column(db.Text)

    def progress_percentage(self):
        total = 0
        filled = 0

        # Steps = 50%
        total += 50
        if self.steps:
            filled += 50

        # Observações + melhorias = 50%
        total += 50
        if self.observations and self.improvements:
            filled += 50

        return int((filled / total) * 100)


class CompletedIncident(db.Model):
    __tablename__ = 'completed_incidents'

    id = db.Column(db.Integer, primary_key=True)
    in_progress_id = db.Column(db.Integer, db.ForeignKey('in_progress_incidents.id'))
    name = db.Column(db.String(200))
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50))

    class_param = db.Column(db.String(100))
    type_param = db.Column(db.String(100))
    steps = db.Column(db.PickleType)
    evidences = db.Column(db.PickleType)
    observations = db.Column(db.Text)
    improvements = db.Column(db.Text)

    in_progress = db.relationship('InProgressIncident', backref='completed_incident')


# Create tables (run once, or use migrations)
with app.app_context():
    db.create_all()


def load_in_progress_incidents():
    return InProgressIncident.query.order_by(InProgressIncident.start_date.desc()).all()

def load_completed_incidents():
    return CompletedIncident.query.filter(CompletedIncident.status != "In Progress").order_by(CompletedIncident.start_date.desc()).all()

def load_incident_steps():
    return load_json(STEPS_PATH)

def load_incidents():
    return load_json(INCIDENTS_PATH)


def check_and_complete_incident(incident_id):
    incident = InProgressIncident.query.get(incident_id)

    if incident and incident.progress_percentage() == 100:
        completed = CompletedIncident(
            in_progress_id=incident.id,
            name=f"{incident.class_param} - {incident.type_param}",
            start_date=incident.start_date,
            end_date=datetime.utcnow(),
            status="Completed",
            class_param=incident.class_param,
            type_param=incident.type_param,
            steps=incident.steps,
            evidences=incident.evidences,
            observations=incident.observations,
            improvements=incident.improvements
        )

        db.session.add(completed)
        db.session.delete(incident)
        db.session.commit()


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
    in_progress_incidents = InProgressIncident.query.order_by(InProgressIncident.start_date.desc()).all()
    incidents = CompletedIncident.query.order_by(CompletedIncident.id.desc()).all()
    return render_template('dashboard.html', in_progress_incidents=in_progress_incidents, incidents=incidents)


@app.route('/complete_incident/<int:incident_id>', methods=['POST'])
def complete_incident(incident_id):
    incident = InProgressIncident.query.get(incident_id)
    if not incident:
        return jsonify({'error': 'Incident not found'}), 404

    completed = CompletedIncident(
        id=incident.id,
        name=incident.name,
        creation_date=incident.creation_date,
        end_date=datetime.utcnow(),
        status='Completed'
    )

    try:
        db.session.add(completed)
        db.session.delete(incident)
        db.session.commit()
        return jsonify({'message': 'Incident marked as completed'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/delete_incident/<int:incident_id>', methods=['POST'])
def delete_incident(incident_id):
    incident = InProgressIncident.query.get(incident_id)
    if incident:
        db.session.delete(incident)
        db.session.commit()
        return jsonify({'message': 'Incident deleted from in-progress'}), 200

    incident = CompletedIncident.query.get(incident_id)
    if incident:
        db.session.delete(incident)
        db.session.commit()
        return jsonify({'message': 'Incident deleted from completed'}), 200

    return jsonify({'error': 'Incident not found'}), 404


def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


@app.route('/incident', methods=['GET', 'POST'])
def incident():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incidents_class = load_json(INCIDENTS_PATH)
    incidents_type = load_json(STEPS_PATH)

    incidents_completed = load_completed_incidents()
    incidents_in_progress = load_in_progress_incidents()

    if request.method == 'POST':
        selected_class = request.form.get('class_')
        selected_type = request.form.get('type_')

        if selected_class and selected_type:
            session['class'] = selected_class
            session['type'] = selected_type
            session['start'] = datetime.utcnow().isoformat()

            try:
                new_incident = InProgressIncident(
                    class_param=selected_class,
                    type_param=selected_type,
                    start_date=datetime.utcnow(),
                    status='In Progress'
                )
                db.session.add(new_incident)
                db.session.commit()

                session['id'] = new_incident.id
                return redirect(url_for('steps'))

            except Exception as e:
                db.session.rollback()
                print("❌ ERROR creating incident:", str(e))
                return redirect(url_for('dashboard'))

        return "Required fields missing", 400

    return render_template(
        'incident.html',
        incidents_completed=incidents_completed,
        incidents_in_progress=incidents_in_progress,
        incidents_type=incidents_type,
        incidents_class=incidents_class,
        show_user_dropdown=True
    )


@app.route('/incident/resume/<int:incident_id>')
def resume_incident(incident_id):
    incident = InProgressIncident.query.get(incident_id)
    if not incident:
        flash("Incident not found.", "warning")
        return redirect(url_for('dashboard'))

    session['incident_id'] = incident.id
    session['incident_class'] = incident.class_param
    session['start_datetime'] = incident.start_date.isoformat() if incident.start_date else datetime.utcnow().isoformat()
    session['selected_type'] = incident.type_param or ''
    session.modified = True

    steps = session.get('steps', [])
    evidences = session.get('evidences', {})

    for i, _ in enumerate(steps, start=1):
        if str(i) not in evidences:
            return redirect(url_for('steps'))

    return redirect(url_for('complete'))


@app.route('/incident/start', methods=['POST'])
def start_incident():
    if 'id' not in session:
        class_param = session.get('class')
        new_incident = InProgressIncident(
            class_param=class_param,
            start_date=datetime.utcnow(),
            status='In Progress'
        )
        db.session.add(new_incident)
        db.session.commit()
        session['id'] = new_incident.id
        session.modified = True
    return jsonify({'status': 'ok', 'id': session['id']})


from flask import session, redirect, url_for, abort, render_template
from datetime import datetime
import json  # só se necessário

@app.route('/incident/steps', methods=['GET', 'POST'])
def steps():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    class_param = session.get('class')
    type_param = session.get('type')

    if not class_param or not type_param:
        return abort(400, description="Parâmetros 'class' e 'type' em falta na sessão.")

    incident_id = session.get('id')
    if not incident_id:
        # Criar novo incidente e guardar no banco
        new_incident = InProgressIncident(
            class_param=class_param,
            type_param=type_param,
            start_date=datetime.now()
        )
        db.session.add(new_incident)
        db.session.commit()
        # Guardar o novo id na sessão para reutilizar
        session['id'] = new_incident.id
        incident = new_incident
    else:
        incident = InProgressIncident.query.get(incident_id)
        if not incident:
            # Caso não exista no DB (ex: id inválido na sessão)
            return abort(404, description="Incidente não encontrado.")

    # Obter os steps para o incidente (a função load_incident_steps() tem que existir)
    incident_steps = load_incident_steps()
    if len(incident_steps) == 1 and isinstance(incident_steps[0], list):
        incident_steps = incident_steps[0]

    found_steps = []

    for item in incident_steps:
        if item.get('class', '').strip().lower() == class_param.strip().lower():
            for type_item in item.get('types', []):
                if type_item.get('type', '').strip().lower() == type_param.strip().lower():
                    found_steps = type_item.get('steps', [])
                    break

    # Guardar steps diretamente (não json.dumps) pois é PickleType
    incident.steps = found_steps
    incident.class_param = class_param
    incident.type_param = type_param
    db.session.commit()

    session['steps'] = found_steps
    session['start'] = session.get('start') or datetime.utcnow().isoformat()
    session.modified = True

    return render_template(
        'steps.html',
        steps=found_steps,
        class_=class_param,
        type_=type_param,
        show_user_dropdown=True
    )



@app.route('/incident/save_step', methods=['POST'])
def save_step():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'Incident ID missing in session'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON data'}), 400

    try:
        step_index = str(int(data.get('step', -1)) + 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid step index'}), 400

    evidence = data.get('evidence', '').strip()
    if not evidence:
        return jsonify({'error': 'Empty evidence'}), 400

    checked_substeps = data.get('checked_substeps', [])
    if not isinstance(checked_substeps, list):
        return jsonify({'error': 'Invalid substeps format'}), 400

    evidences = session.setdefault('evidences', {})
    sub_steps = session.setdefault('sub_steps', {})

    evidences[step_index] = evidence
    sub_steps[step_index] = checked_substeps

    session['evidences'] = evidences
    session['sub_steps'] = sub_steps
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
            start_time = datetime.utcnow()
    elif not start_time:
        start_time = datetime.utcnow()

    end_time = datetime.utcnow()
    session['end'] = end_time

    session['lessons'] = {
        'improvements': improvements,
        'observations': observations
    }

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'incident_id not found in session'}), 400

    try:
        incident = InProgressIncident.query.get(incident_id)
        if not incident:
            return jsonify({'error': 'Incident not found'}), 404

        incident.status = "Completed"
        incident.improvements = improvements
        incident.observations = observations
        incident.start_datetime = start_time
        incident.end_datetime = end_time

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    session['incident_submitted'] = True
    session['report_data'] = {
        'class': session.get('class', 'N/A'),
        'type': session.get('type', 'N/A'),
        'steps': session.get('steps', []),
        'sub_steps': session.get('sub_steps', {}),
        'evidences': session.get('evidences', {}),
        'attachments': session.get('report_data', {}).get('attachments', {})
    }

    session.modified = True
    return jsonify({'status': 'success'}), 200


@app.route('/incident/complete', methods=['GET', 'POST'])
def complete():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    incident_id = session.get('id', '1')

    return render_template('complete.html', incident_id=incident_id, show_user_dropdown=True)


@app.route('/incident/upload_file', methods=['POST'])
def upload_file():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    try:
        step_index = str(int(request.form.get('step', -1)) + 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid step index'}), 400

    incident_id = session.get('id')
    if not incident_id:
        print("❌ ERRO: incident_id não definido na sessão.")
        return jsonify({'error': 'Incident ID missing in session'}), 400

    # Criar caminho para upload
    upload_folder = os.path.join('uploads', str(incident_id), step_index)
    os.makedirs(upload_folder, exist_ok=True)

    # Salvar ficheiro com nome seguro
    filename = secure_filename(file.filename)
    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    # Caminho relativo para uso posterior (e.g., no relatório)
    relative_path = os.path.join('uploads', str(incident_id), step_index, filename)

    # Atualizar anexos na sessão
    report_data = session.get('report_data', {})
    attachments = report_data.get('attachments', {})
    attachments.setdefault(step_index, []).append(relative_path)

    report_data['attachments'] = attachments
    session['report_data'] = report_data
    session.modified = True

    print(f"✅ Ficheiro '{filename}' guardado para incidente #{incident_id}, step {step_index}")
    return jsonify({'status': 'success', 'path': relative_path})


@app.route('/incident/finish', methods=['POST'])
def finish_incident():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'Incident ID missing'}), 400

    incident = InProgressIncident.query.get(incident_id)
    if not incident or incident.user != username:
        return jsonify({'error': 'Not found or not authorized'}), 404

    # Guardar evidências e sub_steps da sessão no modelo
    incident.evidences = json.dumps(session.get('evidences', {}))
    incident.sub_steps = json.dumps(session.get('sub_steps', {}))
    incident.status = "Completed"
    incident.end = datetime.now()

    db.session.commit()

    # Limpar sessão
    session.pop('id', None)
    session.pop('evidences', None)
    session.pop('sub_steps', None)
    session.pop('steps', None)
    session.pop('class', None)
    session.pop('type', None)
    session.pop('start', None)

    return jsonify({'status': 'ok'})



@app.route('/download_report')
def download_report():
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

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

    steps_raw = data.get('steps', [])
    sub_steps = data.get('sub_steps', {})
    evidences = data.get('evidences', {})

    incident_id = session.get('id')
    if not incident_id:
        print("ERRO: incident_id não encontrado na sessão.")
        return redirect(url_for('dashboard'))

    print('INCIDENT ID:', incident_id)

    # Carregar anexos
    upload_base = os.path.join('uploads', str(incident_id))
    attachments = {}

    if os.path.exists(upload_base):
        for step_folder in os.listdir(upload_base):
            step_path = os.path.join(upload_base, step_folder)
            if os.path.isdir(step_path):
                files = os.listdir(step_path)
                if files:
                    attachments[step_folder] = [
                        os.path.join('uploads', str(incident_id), step_folder, f) for f in files
                    ]

    # Estruturar os passos com subpassos, evidências e anexos
    steps_structured = []
    for index, step in enumerate(steps_raw):
        step_index = str(index + 1)
        steps_structured.append({
            'step': step.get('step', f'Step {step_index}'),
            'substeps': sub_steps.get(step_index, []),
            'evidences': evidences.get(step_index, []),
            'attachments': attachments.get(step_index, []),
        })

    dados_template = {
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        'incident_id': incident_id,
        'selected_class': data.get('class', 'N/A'),
        'selected_type': data.get('type', 'N/A'),
        'start_time': start_dt.strftime('%d/%m/%Y %H:%M:%S'),
        'end_time': end_dt.strftime('%d/%m/%Y %H:%M:%S'),
        'steps': steps_structured,
        'improvements': lessons.get('improvements', ''),
        'observations': lessons.get('observations', '')
    }

    basedir = os.path.abspath(os.path.dirname(__file__))
    template_path = os.path.join(basedir, 'word_templates', 'incidentreport_template.docx')

    try:
        pdf_path = gerar_docx_com_dados(dados_template, template_path=template_path)
    except Exception as e:
        print("Erro ao gerar PDF:", e)
        return redirect(url_for('dashboard'))

    filename = f"report_{datetime.now().strftime('%d-%m-%Y')}_{dados_template['selected_type'].replace(' ', '_')}.pdf"

    session.modified = True

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
    # Criação de diretório temporário para guardar o ficheiro Word final
    temp_dir = tempfile.mkdtemp()
    output_docx = os.path.join(temp_dir, 'incidentreport.docx')

    # Carregar template do Word
    doc = DocxTemplate(template_path)

    # Processar imagens para cada passo
    for step in dados.get('steps', []):
        imagens = []
        for path in step.get('attachments', []):
            full_path = os.path.join(os.getcwd(), path)
            if os.path.exists(full_path):
                imagens.append(InlineImage(doc, full_path, width=Inches(3)))
        step['attachments'] = imagens

    # Preencher e guardar documento
    doc.render(dados)
    doc.save(output_docx)

    # Converter para PDF usando LibreOffice
    output_pdf = converter_para_pdf_com_libreoffice(output_docx)

    return output_pdf


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

    os_name = platform.system()
    if os_name == "Windows":
        libreoffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
    elif os_name == "Darwin":
        libreoffice_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

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