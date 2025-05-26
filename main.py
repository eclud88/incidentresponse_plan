from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, jsonify
from datetime import datetime
from fpdf import FPDF
import os
import io
import json

app = Flask(__name__)
app.secret_key = 'secret_key_1234'

# Simulando uma base de dados simples
UTILIZADORES = {
    'admin': 'senha123',
    'usuario': '123456'
}

# Caminho para os ficheiros JSON
CAMINHO_INCIDENTES = os.path.join(os.path.dirname(__file__), 'incidentes.json')
CAMINHO_PASSOS = os.path.join(os.path.dirname(__file__), 'passos_incidentes.json')

def carregar_incidentes():
    with open(CAMINHO_INCIDENTES, 'r', encoding='utf-8') as f:
        return json.load(f)

def carregar_passos_incidentes():
    with open(CAMINHO_PASSOS, 'r', encoding='utf-8') as p:
        return json.load(p)

@app.route('/')
def index():
    data_atual = datetime.now().strftime('%d/%m/%Y')
    return render_template('index.html', data_atual=data_atual)

@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']
    data_atual = datetime.now().strftime('%d/%m/%Y')

    if username in UTILIZADORES and UTILIZADORES[username] == password:
        session.clear()
        return redirect(url_for('incident'))
    else:
        return render_template('index.html', message="Utilizador ou senha inválidos.", data_atual=data_atual)


@app.route('/dashboard')
def dashboard():
    incidentes = obter_incidentes_do_utilizador(current_user.id)
    return render_template('dashboard.html', incidentes=incidentes)




@app.route('/incident', methods=['GET', 'POST'])
def incident():
    incidentes = carregar_incidentes()
    passos_incidentes = carregar_passos_incidentes()

    if isinstance(passos_incidentes[0], list):
        passos_incidentes = passos_incidentes[0]

    passos = []
    classe_selecionada = ''
    tipo_selecionado = ''

    if request.method == 'POST':
        classe_selecionada = request.form.get('classe')
        tipo_selecionado = request.form.get('tipo')

        session['classe'] = classe_selecionada
        session['tipo'] = tipo_selecionado
        session['inicio'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        if classe_selecionada and tipo_selecionado:
            return redirect(url_for('passos', classe_selecionada=classe_selecionada, tipo_selecionado=tipo_selecionado))

        for c in passos_incidentes:
            if c.get('classe') == classe_selecionada:
                for t in c.get('tipos', []):
                    if t.get('tipo') == tipo_selecionado:
                        passos = t.get('passos', [])
                        session['passos'] = passos
                        break

    return render_template(
        'incident.html',
        incidentes=incidentes,
        passos=passos,
        classe=classe_selecionada,
        tipo=tipo_selecionado
    )

@app.route('/incident/passos', methods=['GET', 'POST'])
def passos():
    session['inicio_registo'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    passos_incidentes = carregar_passos_incidentes()

    if isinstance(passos_incidentes, list) and len(passos_incidentes) == 1 and isinstance(passos_incidentes[0], list):
        passos_incidentes = passos_incidentes[0]

    if request.method == 'GET':
        classe_req = request.args.get('classe')
        tipo_req = request.args.get('tipo')
    else:
        classe_req = request.form.get('classe') or (request.get_json() or {}).get('classe')
        tipo_req = request.form.get('tipo') or (request.get_json() or {}).get('tipo')

    if not classe_req or not tipo_req:
        return abort(400, description="Parâmetros 'classe' e 'tipo' são obrigatórios.")

    passos_encontrados = None
    for classe in passos_incidentes:
        if classe.get('classe', '').lower() == classe_req.lower():
            for tipo in classe.get('tipos', []):
                if tipo.get('tipo', '').lower() == tipo_req.lower():
                    passos_encontrados = tipo.get('passos', [])
                    break
            if passos_encontrados:
                break

    if not passos_encontrados:
        return abort(404, description="Plano de passos não encontrado para a classe e tipo informados.")

    session['classe'] = classe_req
    session['tipo'] = tipo_req
    session['passos'] = passos_encontrados

    return render_template('passos.html', passos=passos_encontrados, classe=classe_req, tipo=tipo_req)

@app.route('/incident/salvar_passo', methods=['POST'])
def salvar_passo():
    dados = request.get_json()
    passo_index = str(dados.get('passo'))
    evidencia = dados.get('evidencia', '').strip()

    if not evidencia:
        return jsonify({'erro': 'Evidência vazia'}), 400

    if 'evidencias' not in session:
        session['evidencias'] = {}

    session['evidencias'][passo_index] = evidencia
    session.modified = True
    return {'status': 'ok'}

@app.route('/salvar_finalizacao', methods=['POST'])
def salvar_finalizacao():
    if not request.is_json:
        return {'erro': 'Formato JSON esperado'}, 400

    data = request.get_json()
    evidencia = data.get('evidencia', '').strip()
    passo = str(data.get('passo'))

    melhorias = data.get('melhorias', '').strip()
    observacoes = data.get('observacoes', '').strip()

    if not melhorias or not observacoes:
        return {'erro': 'Todos os campos devem ser preenchidos'}, 400

    if 'evidencias' not in session:
        session['evidencias'] = {}

    session['evidencias'][passo] = evidencia
    session['licoes'] = {
        'melhorias': melhorias,
        'observacoes': observacoes
    }

    session['fim'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    session.modified = True
    return '', 204



@app.route('/incident/finalizar', methods=['GET', 'POST'])
def finalizar():
    return render_template('finalizar.html')

@app.route('/incident/relatorio')
def relatorio():
    classe = session.get('classe', 'N/D')
    tipo = session.get('tipo', 'N/D')
    passos = session.get('passos', [])
    evidencias = session.get('evidencias', {})
    inicio_registo = session.get('inicio_registo', 'N/D')
    fim = session.get('fim', datetime.now().strftime('%d/%m/%Y %H:%M:%S'))
    licoes = session.get('licoes', {})

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 10, "Relatório do Incidente", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Arial", '', 12)
    pdf.cell(0, 10, f"Classe do Incidente: {classe}", ln=True)
    pdf.cell(0, 10, f"Tipo de Incidente: {tipo}", ln=True)
    pdf.cell(0, 10, f"Início do Registo: {inicio_registo}", ln=True)
    pdf.cell(0, 10, f"Término do Registo: {fim}", ln=True)
    pdf.ln(10)

    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Passos Executados:", ln=True)
    pdf.ln(5)

    for idx, passo in enumerate(passos):
        evidencia = evidencias.get(str(idx), '').split('\n')
        pdf.set_font("Arial", 'B', 12)
        pdf.multi_cell(0, 10, f"Passo {idx + 1}: {passo}")
        pdf.set_font("Arial", '', 12)
        for linha in evidencia:
            pdf.cell(10)
            pdf.cell(0, 10, f"- {linha.strip()}", ln=True)
        pdf.ln(3)

    if licoes:
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Lições Aprendidas:", ln=True)
        pdf.set_font("Arial", '', 12)
        pdf.multi_cell(0, 10, f"Melhorias: {licoes.get('melhorias', '')}")
        pdf.multi_cell(0, 10, f"Observações: {licoes.get('observacoes', '')}")
        pdf.ln(10)

    pdf.set_y(-30)
    pdf.set_font("Arial", 'I', 10)
    data_hoje = datetime.now().strftime('%d/%m/%Y')
    pdf.cell(0, 10, f"Este relatório foi gerado automaticamente através da aplicação no dia {data_hoje}.", align="L")
    pdf.cell(0, 10, f"Página {pdf.page_no()}", align="R")

    pdf_bytes = pdf.output(dest='S').encode('latin-1')
    pdf_stream = io.BytesIO(pdf_bytes)

    agora = datetime.now().strftime("%d-%m-%Y_%H%M%S")
    nome_arquivo = f"relatorio_{agora}_{tipo}.pdf"

    session.clear()  # limpar a sessão só depois de gerar
    return send_file(pdf_stream, mimetype='application/pdf', download_name=nome_arquivo, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
