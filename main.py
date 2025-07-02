from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from fpdf import FPDF
from docxtpl import DocxTemplate, InlineImage
import os
import io
import json


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key')


basedir = os.path.abspath(os.path.dirname(__file__))
os.makedirs(os.path.join(app.root_path, 'reports'), exist_ok=True)


app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # True se usares HTTPS
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
db = SQLAlchemy(app)


USERS = {
    'admin': 'senha123',
    'user': '123456'
}


INCIDENTS_PATH = os.path.join(basedir, 'incidents.json')
STEPS_PATH = os.path.join(basedir, 'incident_steps.json')


class Incident(db.Model):
    __tablename__ = 'incident'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    incident_class = db.Column(db.String(100))
    incident_type = db.Column(db.String(100))
    steps = db.Column(db.Text)
    sub_steps = db.Column(db.Text)
    evidences = db.Column(db.Text)
    attachments = db.Column(db.Text)
    upload_status = db.Column(db.Boolean, default=False)
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='Completed')
    improvements = db.Column(db.Text)
    observations = db.Column(db.Text)
    start_datetime = db.Column(db.DateTime, default=datetime.utcnow)
    end_datetime = db.Column(db.DateTime, nullable=True)


class IncidentStep(db.Model):
    __tablename__ = 'incident_step'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id'), nullable=False, default=1)
    steps = db.Column(db.String(100))
    step_index = db.Column(db.Integer)
    incident_class = db.Column(db.String(100))
    incident_type = db.Column(db.String(100))
    evidence = db.Column(db.Text)
    sub_steps = db.Column(db.Text)
    attachment_name = db.Column(db.String(255))
    start_datetime = db.Column(db.DateTime, default=datetime.utcnow)
    improvements = db.Column(db.Text)
    observations = db.Column(db.Text)
    percent_complete = db.Column(db.Integer, default=0.0)
    status = db.Column(db.String(50), default='In Progress')

    incident = db.relationship('Incident', backref=db.backref('steps_data', lazy=True))

    @staticmethod
    def recalc_incident_progress(incident_id):
        """Recalcula e atualiza status do Incident."""
        inc = Incident.query.get(incident_id)
        if not inc:
            return
        pct = inc.progress_percentage()
        if pct == 100.0:
            inc.status = 'Completed'
            inc.end_datetime = datetime.utcnow()
        db.session.commit()
        return pct

    def progress_percentage(self):
        """50% pelos steps concluídos + 50% por improvements+observations."""
        # 1) Steps
        try:
            step_list = json.loads(self.steps or '[]')
            ev = json.loads(self.evidences or '{}')
            at = json.loads(self.attachments or '{}')
            total = len(step_list)
            if total:
                done = sum(
                    1 for i in range(1, total + 1)
                    if ev.get(str(i), '').strip() and at.get(str(i), [])
                )
                step_score = (done / total) * 50
            else:
                step_score = 0.0
        except Exception:
            step_score = 0.0

        # 2) Lessons learned
        text_score = 50.0 if (self.improvements and self.observations) else 0.0

        return int(round(step_score + text_score))

    def get_ordered_step_keys(self):
        try:
            steps = json.loads(self.steps or '[]')
            return [str(i + 1) for i in range(len(steps))]
        except Exception:
            return []

    def update_next_incomplete_step(self):
        sub_steps = json.loads(self.sub_steps or '{}')
        evidences = json.loads(self.evidences or '{}')
        attachments = json.loads(self.attachments or '{}')
        ordered_steps = self.get_ordered_step_keys()  # ['1', '2', '3', ...]

        for step in ordered_steps:
            has_substeps = len(sub_steps.get(step, [])) > 0
            has_evidence = len(evidences.get(step, '').strip()) > 0
            has_attachment = len(attachments.get(step, [])) > 0

            if not (has_substeps and has_evidence and has_attachment):
                self.current_step = step
                db.session.add(self)
                db.session.commit()

                return

        self.current_step = None  # Tudo completo
        db.session.add(self)
        db.session.commit()

    def is_complete(self):
        sub_steps = json.loads(self.sub_steps or '{}')
        evidences = json.loads(self.evidences or '{}')
        attachments = json.loads(self.attachments or '{}')
        ordered_steps = self.get_ordered_step_keys()

        for step in ordered_steps:
            if not (
                len(sub_steps.get(step, [])) > 0 and
                len(evidences.get(step, '').strip()) > 0 and
                len(attachments.get(step, [])) > 0
            ):
                return False
        return True

    def get_next_step_from_data(self):
        try:
            step_keys = self.get_ordered_step_keys()  # ['1', '2', '3', ...]

            for step_index in step_keys:
                step = IncidentStep.query.filter_by(incident_id=self.id, step_index=step_index).first()

                if not step or not (
                        step.evidence and step.sub_steps and step.attachment_name
                        and step.sub_steps != '[]' and step.attachment_name.strip() != ''
                ):
                    return int(step_index) == 1  # step incompleto encontrado

            # Se todos os steps estiverem completos, mas ainda faltar lessons learned
            if not (self.improvements and self.observations):
                return "lessons_learned"

            return None

        except Exception as e:
            print("Erro ao identificar próximo passo com base nos dados:", e)
            return None

    @staticmethod
    def restore_incident_to_session(incident, session):
        session['incident_id'] = incident.id
        session['start_datetime'] = (
            incident.start_datetime.isoformat()
            if incident.start_datetime else datetime.utcnow().isoformat()
        )

        # Restaurar class e type
        session['class'] = incident.incident_class
        session['type'] = incident.incident_type

        # Restaurar os títulos dos steps
        try:
            steps_data = json.loads(incident.steps or '[]')
            session['steps'] = [step.get('title', f'Step {i + 1}') for i, step in enumerate(steps_data)]
        except json.JSONDecodeError:
            session['steps'] = []

        # Restaurar os IncidentStep específicos do incidente
        step_data = IncidentStep.query.filter_by(incident_id=incident.id).all()

        evidences = {}
        sub_steps = {}
        attachments = {}

        for sd in step_data:
            # Usar step_index para chavear cada passo individualmente
            idx = str(getattr(sd, 'step_index', None) or sd.id)  # fallback para id se step_index não existir

            if sd.evidence:
                evidences[idx] = sd.evidence

            if sd.sub_steps:
                try:
                    sub_steps[idx] = json.loads(sd.sub_steps)
                except json.JSONDecodeError:
                    sub_steps[idx] = []

            if sd.attachment_name:
                attachments[idx] = [sd.attachment_name]  # em lista, como esperado no template

        session['evidences'] = evidences
        session['sub_steps'] = sub_steps
        session['attachments'] = attachments

        session.modified = True


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
    path = os.path.join(app.root_path, 'relatorios', f'incident_{incident_id}.pdf')

    if not os.path.exists(path):
        return '', 404 if request.method == 'HEAD' else abort(404)

    if request.method == 'HEAD':
        return '', 200

    return send_file(path, as_attachment=True, download_name=f'incident_{incident_id}.pdf')


@app.route('/incident/delete/<int:incident_id>', methods=['POST'])
def delete_incident(incident_id):
    try:
        # Remove associated steps
        IncidentStep.query.filter_by(incident_id=incident_id).delete()

        # Remove incident
        incident = Incident.query.get_or_404(incident_id)
        db.session.delete(incident)
        db.session.commit()

        return jsonify({'status': 'success'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500



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
    message = 'Incorrect user or password!'
    return render_template('index.html', message=message)


@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('index'))

    steps = IncidentStep.query.order_by(IncidentStep.start_datetime.desc()).all()

    grouped = {}
    for step in steps:
        grouped.setdefault(step.incident_id, []).append(step)

    incidents = []
    for incident_id, step_list in grouped.items():
        first = sorted(step_list, key=lambda s: s.start_datetime)[0] if step_list else None

        completed = [
            s for s in step_list if s.evidence and s.sub_steps and s.attachment_name
        ]
        total = len(step_list)
        step_percent = (len(completed) / total * 50) if total else 0

        text_percent = 0
        if first:
            if first.observations and first.observations.strip():
                text_percent += 25
            if first.improvements and first.improvements.strip():
                text_percent += 25

        percent = round(step_percent + text_percent, 1)

        incidents.append({
            'incident_id': incident_id,
            'class': first.incident_class if first else '',
            'type': first.incident_type if first else '',
            'start_datetime': first.start_datetime if first else '',
            'status': first.status if first else '',
            'percent': percent,
        })

    in_progress = [i for i in incidents if i['percent'] < 100.0]
    completed = [i for i in incidents if i['percent'] == 100.0]

    return render_template(
        'dashboard.html',
        in_progress_incidents=in_progress,
        completed_incidents=completed,
        username=session['username'],
        show_user_dropdown=True
    )


@app.route('/incident', methods=['GET', 'POST'])
def incident():
    if 'username' not in session:
        return redirect(url_for('index'))

    incidents = load_incidents()

    if request.method == 'POST':
        selected_class = request.form.get('class_')
        selected_type = request.form.get('type_')

        if selected_class and selected_type:
            session.update({
                'class': selected_class,
                'type': selected_type,
                'start': datetime.now()
            })

            try:
                last = IncidentStep.query.order_by(IncidentStep.incident_id.desc()).first()
                next_id = 1 if not last else last.incident_id + 1

                new_incident = IncidentStep(
                    incident_id=next_id,
                    incident_class=selected_class,
                    incident_type=selected_type,
                    start_datetime=session['start'],
                    status="In Progress"
                )
                db.session.add(new_incident)
                db.session.commit()
                session['id'] = new_incident.id

                return redirect(url_for(
                    'steps',
                    start_datetime=session['start'],
                    class_=selected_class,
                    type_=selected_type
                ))

            except Exception as e:
                db.session.rollback()
                return jsonify({'status': 'error', 'message': str(e)}), 500

    return render_template(
        'incident.html',
        incidents=incidents,
        show_user_dropdown=True
    )


@app.route('/incident/resume/<int:incident_id>')
def resume_incident(incident_id):
    incident = IncidentStep.query.get(incident_id)
    if not incident:
        flash("Incident not found.", "warning")
        return redirect(url_for('dashboard'))

    IncidentStep.restore_incident_to_session(incident, session)
    step = IncidentStep.query.filter_by(incident_id=incident.id).first()

    if not step:
        flash("Incident step data missing.", "danger")
        return redirect(url_for('dashboard'))

    next_step = step.get_next_step_from_data()
    if next_step is None or next_step == "lessons_learned":
        flash("All steps are completed!" if next_step is None else "", "info")
        return redirect(url_for('complete', incident_id=incident.id))

    try:
        step_id = int(next_step)
        return redirect(url_for('step_view', step_id=step_id, incident_id=incident.id))
    except (ValueError, TypeError):
        flash("Invalid next step format.", "danger")
        return redirect(url_for('dashboard'))


@app.route('/incident/<int:incident_id>/step/<int:step_id>', methods=['GET'])
def step_view(incident_id, step_id):
    if 'username' not in session:
        return redirect(url_for('index'))

    incident = db.session.get(IncidentStep, incident_id)
    if not incident:
        flash("Incident not found.", "warning")
        return redirect(url_for('dashboard'))

    # Restore session if needed
    if session.get('incident_id') != incident_id:
        IncidentStep.restore_incident_to_session(incident, session)

    steps_titles = session.get('steps', [])
    try:
        sub_steps_list = json.loads(incident.sub_steps or '[]')
    except (ValueError, TypeError):
        sub_steps_list = []

    evidences = session.get('evidences', {})
    attachments = session.get('attachments', {})

    steps = []
    for idx, title in enumerate(steps_titles):
        substeps_for_step = sub_steps_list[idx] if isinstance(sub_steps_list, list) and idx < len(sub_steps_list) else []
        steps.append({'step': title, 'sub_steps': substeps_for_step})

    # Extract first file from attachments per step
    existing_files = {k: v[0] for k, v in attachments.items() if isinstance(v, list) and v}

    # Incident properties, fallback to None if missing
    class_ = getattr(incident, 'class_', None)
    type_ = getattr(incident, 'type_', None)

    # `percent` is not defined here in original, assuming 0 or calculate if needed
    percent = getattr(incident, 'percent_complete', 0)

    return render_template(
        'steps.html',
        steps=steps,
        evidences=evidences,
        sub_steps=sub_steps_list,
        existing_files=existing_files,
        percent=percent,
        class_=class_,
        type_=type_,
        incident_id=incident_id
    )


@app.route('/incident/steps', methods=['GET', 'POST'])
def steps():
    if 'username' not in session:
        return redirect(url_for('index'))

    incident_id = session.get('id')
    class_param = session.get('class')
    type_param = session.get('type')

    if not class_param or not type_param:
        abort(400, description="Missing 'class' or 'type' parameters in session.")

    # Load step definitions (from JSON or other source)
    all_steps_data = load_incident_steps()
    if len(all_steps_data) == 1 and isinstance(all_steps_data[0], list):
        all_steps_data = all_steps_data[0]

    found_steps = []
    for item in all_steps_data:
        if item.get('class', '').strip().lower() == class_param.strip().lower():
            for type_item in item.get('types', []):
                if type_item.get('type', '').strip().lower() == type_param.strip().lower():
                    found_steps = type_item.get('steps', [])
                    break

    session['total_steps'] = len(found_steps)

    if not incident_id:
        # No incident in session → create new one
        incident = IncidentStep(
            incident_class=class_param,
            incident_type=type_param,
            steps=json.dumps(found_steps),
            status="In Progress",
            start_datetime=datetime.utcnow()
        )
        db.session.add(incident)
        db.session.commit()
        session['id'] = incident.id
        incident_id = incident.id

    else:
        # incident_id present, try to load from DB
        incident = Incident.query.get(incident_id)

        if not incident:
            # Incident not found in DB, create a new one instead of using id=1 forcibly
            incident = IncidentStep(
                incident_class=class_param,
                incident_type=type_param,
                steps=json.dumps(found_steps),
                status="In Progress",
                start_datetime=datetime.utcnow()
            )
            db.session.add(incident)
            db.session.commit()
            session['id'] = incident.id
            incident_id = incident.id
        else:
            # Update existing incident if needed
            updated = False
            if incident.incident_class != class_param:
                incident.incident_class = class_param
                updated = True
            if incident.incident_type != type_param:
                incident.incident_type = type_param
                updated = True
            if json.loads(incident.steps or "[]") != found_steps:
                incident.steps = json.dumps(found_steps)
                updated = True
            if incident.status != "In Progress":
                incident.status = "In Progress"
                updated = True
            if updated:
                db.session.commit()

    # Retrieve all steps for this incident
    steps_data = IncidentStep.query.filter_by(incident_id=incident_id).all()

    evidences = {str(s.step_index): s.evidence for s in steps_data if s.evidence}
    sub_steps = {str(s.step_index): json.loads(s.sub_steps or "[]") for s in steps_data if s.sub_steps}
    existing_files = {str(s.step_index): s.attachment_name for s in steps_data if s.attachment_name}

    saved_indices = [s.step_index for s in steps_data if s.step_index is not None]
    start_index = max(saved_indices) if saved_indices else 0

    # Calculate progress
    completed_steps = [
        s for s in steps_data if s.evidence and s.sub_steps and s.attachment_name
    ]
    total_steps = len(found_steps)
    step_progress = round((len(completed_steps) / total_steps) * 50.0, 1) if total_steps else 0.0

    lessons_progress = 0.0
    if incident.observations and incident.observations.strip():
        lessons_progress += 25.0
    if incident.improvements and incident.improvements.strip():
        lessons_progress += 25.0

    percent = round(step_progress + lessons_progress, 1)

    # Update session info
    session['session_inprogress'] = str(incident_id)
    session['start'] = session.get('start') or datetime.utcnow().isoformat()
    session.modified = True

    return render_template(
        'steps.html',
        incident=incident,
        steps=found_steps,
        class_=class_param,
        type_=type_param,
        show_user_dropdown=True,
        start_index=start_index,
        evidences=evidences,
        sub_steps=sub_steps,
        upload_status={},  # If used, otherwise remove
        existing_files=existing_files,
        percent=percent
    )


def calcular_progresso(incident_id):
    incident = IncidentStep.query.get(incident_id)
    if not incident:
        return jsonify({'error': 'Incident not found'}), 404

    total_steps = session.get('total_steps') or len(json.loads(incident.steps or "[]"))

    completed_steps = IncidentStep.query.filter(
        IncidentStep.incident_id == incident_id,
        IncidentStep.evidence.isnot(None), IncidentStep.evidence != '',
        IncidentStep.sub_steps.isnot(None), IncidentStep.sub_steps != '[]',
        IncidentStep.attachment_name.isnot(None), IncidentStep.attachment_name != ''
    ).count()

    step_progress = round((completed_steps / total_steps) * 50, 1) if total_steps else 0.0

    extra_progress = 0
    if incident.observations and incident.observations.strip():
        extra_progress += 25
    if incident.improvements and incident.improvements.strip():
        extra_progress += 25

    percent = round(step_progress + extra_progress, 2)
    incident.percent_complete = percent
    incident.status = "Complete" if percent == 100.0 else "In Progress"
    db.session.commit()

    print(f"[Progress] {completed_steps}/{total_steps} steps — {percent:.1f}% complete.")
    return jsonify({'status': 'ok', 'percent': percent})



@app.route('/incident/save_step', methods=['POST'])
def save_step():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'Incident ID missing in session'}), 400

    data = request.get_json(silent=True) or {}

    # Emulate GET progress if no data sent
    if not data:
        return calcular_progresso(incident_id)

    # Validate step_index
    try:
        step_index = int(data.get('step'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid step index'}), 400

    evidence = data.get('evidence', '').strip()
    if not evidence:
        return jsonify({'error': 'Empty evidence'}), 400

    checked_substeps = data.get('checked_substeps', [])
    if not isinstance(checked_substeps, list):
        return jsonify({'error': 'Invalid substeps format'}), 400

    attachment_name = data.get('attachment_name', '').strip()

    try:
        # Use IncidentStep for step-specific data
        step = IncidentStep.query.filter_by(incident_id=incident_id, step_index=step_index).first()
        if not step:
            step = IncidentStep(incident_id=incident_id, step_index=step_index)
            db.session.add(step)

        step.evidence = evidence
        step.sub_steps = json.dumps(checked_substeps)
        if attachment_name:
            step.attachment_name = attachment_name

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    # Call progress calculator
    progress_response = calcular_progresso(incident_id)

    # Extract the Response object from tuple if needed
    if isinstance(progress_response, tuple):
        progress_response_obj = progress_response[0]
    else:
        progress_response_obj = progress_response

    progress_data = progress_response_obj.get_json()

    # Check for 'percent' key, else return error
    if 'percent' not in progress_data:
        return jsonify({'error': progress_data.get('error', 'Unknown error')}), 400

    return jsonify(status="ok", percent=progress_data['percent'])



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

    # Datas
    start_time = session.get('start')
    if isinstance(start_time, str):
        try:
            start_time = datetime.fromisoformat(start_time)
        except ValueError:
            start_time = datetime.now()
    elif not start_time:
        start_time = datetime.now()

    end_time = datetime.now()
    session['end'] = end_time

    # Lessons learned
    session['lessons'] = {
        'improvements': improvements,
        'observations': observations
    }

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'incident_id not found in session'}), 400

    try:
        incident = db.session.get(Incident, incident_id)

        if not incident:
            return jsonify({'error': 'Incident not found'}), 404

        incident.status = "Completed"
        incident.improvements = improvements
        incident.observations = observations
        incident.start_datetime = start_time
        incident.end_datetime = end_time

        def update_incident_progress(incident_step_id, improvements, observations):
            incident_step = IncidentStep.query.get(incident_step_id)
            if not incident_step:
                return 0.0

            if improvements and observations:
                incident_step.percent_complete = 100.0
            elif improvements or observations:
                incident_step.percent_complete = 75.0

            return IncidentStep.progress_percentage(incident_step)

        # ✅ Atualiza percentagem considerando lessons e steps
        percent = update_incident_progress(incident_id, improvements, observations)
        incident.percent_complete = percent

        db.session.commit()
        print(f"[✔️] Incident #{incident_id} atualizado com sucesso com {percent}%.")

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    # Guardar info para o relatório
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


@app.route('/incident/complete')
@app.route('/incident/complete/<int:incident_id>')
def complete(incident_id=None):
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))

    if not incident_id:
        incident_id = session.get('id', '1')

    incident = IncidentStep.query.get_or_404(incident_id)
    session['incident_id'] = incident.id
    session.modified = True
    return render_template('complete.html', incident=incident, incident_id=incident.id)


@app.route('/incident/upload_file', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'})

    file = request.files['file']
    step_index = request.form.get('step')

    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'})

    if not step_index or not step_index.isdigit():
        return jsonify({'status': 'error', 'message': 'Invalid step index'})

    step_index = int(step_index)  # convert here!

    filename = secure_filename(file.filename)
    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'status': 'error', 'message': 'No incident in session'})

    try:
        upload_dir = os.path.join(app.root_path, 'uploads', str(incident_id), f"step_{step_index}")
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        relative_path = f"uploads/{incident_id}/step_{step_index}/{filename}"

        step = IncidentStep.query.filter_by(incident_id=incident_id, step_index=step_index).first()
        if not step:
            step = IncidentStep(incident_id=incident_id, step_index=step_index)
            db.session.add(step)

        step.attachment_name = relative_path
        step.upload_status = True
        db.session.commit()

        return jsonify({'status': 'success', 'file': relative_path})

    except Exception as e:
        db.session.rollback()
        print("Upload error:", e)
        return jsonify({'status': 'error', 'message': str(e)})



@app.route('/incident/finish', methods=['POST'])
def finish_incident():
    username = session.get('username')
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    incident_id = session.get('id')
    if not incident_id:
        return jsonify({'error': 'Incident ID missing'}), 400

    incident = db.session.get(IncidentStep, incident_id)

    if not incident or incident.user != username:
        return jsonify({'error': 'Not found or not authorized'}), 404

    # Guardar evidências e sub_steps da sessão no modelo
    incident.evidences = json.dumps(session.get('evidences', {}))
    incident.sub_steps = json.dumps(session.get('sub_steps', {}))
    incident.status = "Completed"
    incident.end_datetime = datetime.now()

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

    incident_id = session.get('incident_id')
    if not incident_id:
        incident_id = 1


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
        pdf_path_temp = gerar_docx_com_dados(dados_template, template_path=template_path)

        # Guardar permanentemente
        permanent_dir = os.path.join(app.root_path, 'relatorios')
        os.makedirs(permanent_dir, exist_ok=True)

        final_pdf_path = os.path.join(permanent_dir, f'incident_{incident_id}.pdf')
        shutil.copy(pdf_path_temp, final_pdf_path)

    except Exception as e:
        print("Erro ao gerar PDF:", e)
        return redirect(url_for('dashboard'))

    filename = f"report_{datetime.now().strftime('%d-%m-%Y')}_{dados_template['selected_type'].replace(' ', '_')}.pdf"

    session.modified = True

    return send_file(final_pdf_path, as_attachment=True, download_name=filename)



def flatten_data(data):
    """Função recursiva que transforma objetos aninhados em texto para pesquisa."""
    if isinstance(data, dict):
        return ' '.join(flatten_data(v) for v in data.values())
    elif isinstance(data, list):
        return ' '.join(flatten_data(item) for item in data)
    else:
        return str(data)


@app.route('/incident/<int:incident_id>/download', methods=['GET', 'HEAD'])
def download_incident(incident_id):
    filepath = os.path.join(app.root_path, 'reports', f'incident_{incident_id}.pdf')

    if not os.path.exists(filepath):
        return '', 404 if request.method == 'HEAD' else abort(404)

    if request.method == 'HEAD':
        return '', 200

    return send_file(filepath, as_attachment=True, download_name=f'incident_{incident_id}.pdf')


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


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
