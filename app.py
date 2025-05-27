import os
import math
import openpyxl
import ezdxf
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
from collections import defaultdict
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import jsonify
from flask_cors import CORS
from flask import session


app = Flask(__name__)
CORS(app)

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'dxf'}
app.config['PRICE_FILE'] = 'price.xlsx'
app.secret_key = 'your-secret-key-here'

app.config.update(
    MAIL_SERVER='smtp.mail.ru',
    MAIL_PORT=465,
    MAIL_USE_SSL=True,
    MAIL_USERNAME='ponomera3d@mail.ru',
    MAIL_PASSWORD='LiBS4RQUaqZ7BJLNidVq',
    MAIL_DEFAULT_SENDER='ponomera3d@mail.ru',
    ADMIN_EMAIL='ponomera3d@mail.ru'
)

# Создаем папку uploads, если её нет
if not os.path.exists('uploads'):
    os.makedirs('uploads')

def parse_price_table():
    try:
        wb = openpyxl.load_workbook(app.config['PRICE_FILE'])
        sheet = wb.active
        
        materials = []
        for idx, cell in enumerate(sheet[1]):
            if idx == 0:
                continue
            if cell.value:
                name = ' '.join(str(cell.value).replace('\n', ' ').split())
                materials.append(name.strip())
        
        # Собираем все уникальные толщины
        thicknesses = set()
        price_data = {material: {} for material in materials}
        
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None:
                continue
            
            thickness = float(row[0]) if isinstance(row[0], (int, float)) else row[0]
            thicknesses.add(thickness)
            
            for col_idx in range(1, len(materials) + 1):
                if len(row) > col_idx and isinstance(row[col_idx], (int, float)):
                    material = materials[col_idx - 1]
                    price_data[material][thickness] = float(row[col_idx])
        
        # Конвертируем set в отсортированный список
        sorted_thickness = sorted(thicknesses)
        
        return {
            'materials': materials,
            'prices': price_data,
            'sorted_thickness': sorted_thickness
        }
    
    except Exception as e:
        print(f"Ошибка загрузки прайса: {e}")
        return {
            'materials': [],
            'prices': {},
            'sorted_thickness': []
        }

def calculate_cutting_cost_with_warning(material, thickness, length, cut_points):
    """Возвращает стоимость и предупреждение (если нужно)"""
    if not hasattr(app, 'price_data'):
        app.price_data = parse_price_table()
    
    prices = app.price_data.get('prices', {}).get(material, {})
    
    if not prices:
        return 0, f"Для материала '{material}' нет данных в прайсе"
    
    available_thicknesses = []
    for t in prices.keys():
        try:
            t_float = float(t) if isinstance(t, str) else t
            available_thicknesses.append(t_float)
        except (ValueError, TypeError):
            continue
    
    if not available_thicknesses:
        return 0, f"Нет доступных толщин для материала '{material}'"
    
    try:
        thickness_float = float(thickness)
    except (ValueError, TypeError):
        return 0, "Некорректное значение толщины"
    
    selected_thickness = None
    warning = None
    
    # Проверяем точное совпадение
    for t in available_thicknesses:
        if math.isclose(t, thickness_float, rel_tol=0.01):
            selected_thickness = t
            break
    
    # Если точного совпадения нет
    if selected_thickness is None:
        larger_thicknesses = [t for t in available_thicknesses if t >= thickness_float]
        if larger_thicknesses:
            selected_thickness = min(larger_thicknesses)
            warning = f"Для {material} {thickness} мм используется цена для {selected_thickness} мм"
        else:
            selected_thickness = max(available_thicknesses)
            warning = f"Для {material} {thickness} мм используется цена для {selected_thickness} мм (максимальная доступная)"
    
    price_per_meter = prices.get(selected_thickness) or prices.get(str(selected_thickness))
    if price_per_meter is None:
        return 0, f"Не удалось найти цену для толщины {selected_thickness} мм"
    
    base_price = price_per_meter * (length / 1000)
    cut_cost = max(0, cut_points - 5) * 6
    
    return round(base_price + cut_cost, 2), warning

def calculate_total_length(doc):
    """
    Calculate the total length of lines in a DXF document.
    Supports LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC, SPLINE.
    """
    total_length = 0.0
    msp = doc.modelspace()
    for entity in msp:
        try:
            length = 0.0
            if entity.dxftype() == 'LINE':
                length = calculate_line_length(entity)
            elif entity.dxftype() in ['LWPOLYLINE', 'POLYLINE']:
                length = calculate_polyline_length(entity)
            elif entity.dxftype() == 'CIRCLE':
                length = calculate_circle_length(entity)
            elif entity.dxftype() == 'ARC':
                length = calculate_arc_length(entity)
            elif entity.dxftype() == 'SPLINE':
                length = calculate_spline_length(entity)
            total_length += length
        except Exception as e:
            print(f"Error processing {entity.dxftype()}: {e}")
            continue
    return total_length

def calculate_line_length(entity):
    start = entity.dxf.start
    end = entity.dxf.end
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    return length

def calculate_polyline_length(entity):
    length = 0.0
    points = list(entity.get_points())
    for i in range(len(points) - 1):
        start = points[i]
        end = points[i + 1]
        length += math.hypot(end[0] - start[0], end[1] - start[1])
    if entity.closed:
        start = points[-1]
        end = points[0]
        length += math.hypot(end[0] - start[0], end[1] - start[1])
    return length

def calculate_circle_length(entity):
    radius = entity.dxf.radius
    return 2 * math.pi * radius

def calculate_arc_length(entity):
    radius = entity.dxf.radius
    start_angle = math.radians(entity.dxf.start_angle)
    end_angle = math.radians(entity.dxf.end_angle)
    angle = end_angle - start_angle
    if angle < 0:
        angle += 2 * math.pi
    return radius * angle

def calculate_spline_length(entity):
    length = 0.0
    spline_points = entity.approximate(segments=100)
    for i in range(len(spline_points) - 1):
        start = spline_points[i]
        end = spline_points[i + 1]
        length += math.hypot(end[0] - start[0], end[1] - start[1])
    return length

def calculate_bounding_box_area(doc):
    """
    Calculate the area of the minimal bounding rectangle for all entities in the DXF document.
    """
    msp = doc.modelspace()
    min_x, min_y, max_x, max_y = None, None, None, None
    for entity in msp:
        try:
            vertices = get_entity_vertices(entity)
            for x, y in vertices:
                min_x = x if min_x is None else min(min_x, x)
                min_y = y if min_y is None else min(min_y, y)
                max_x = x if max_x is None else max(max_x, x)
                max_y = y if max_y is None else max(max_y, y)
        except Exception as e:
            print(f"Error processing {entity.dxftype()}: {e}")
            continue
    if None not in (min_x, min_y, max_x, max_y):
        width = max_x - min_x
        height = max_y - min_y
        area = width * height / 1_000_000  # Convert from mm² to m²
        return area
    else:
        return 0.0

def get_entity_vertices(entity):
    vertices = []
    if entity.dxftype() == 'LINE':
        vertices.extend([entity.dxf.start[:2], entity.dxf.end[:2]])
    elif entity.dxftype() in ['LWPOLYLINE', 'POLYLINE']:
        vertices.extend([point[:2] for point in entity.get_points()])
    elif entity.dxftype() == 'CIRCLE':
        center = entity.dxf.center
        radius = entity.dxf.radius
        vertices.extend([
            (center[0] - radius, center[1]),
            (center[0] + radius, center[1]),
            (center[0], center[1] - radius),
            (center[0], center[1] + radius),
        ])
    elif entity.dxftype() == 'ARC':
        center = entity.dxf.center
        radius = entity.dxf.radius
        start_angle = math.radians(entity.dxf.start_angle)
        end_angle = math.radians(entity.dxf.end_angle)
        vertices.extend([
            (
                center[0] + radius * math.cos(start_angle),
                center[1] + radius * math.sin(start_angle)
            ),
            (
                center[0] + radius * math.cos(end_angle),
                center[1] + radius * math.sin(end_angle)
            )
        ])
    elif entity.dxftype() == 'SPLINE':
        spline_points = list(entity.flattening(distance=0.01))  # Аппроксимация сплайна
        vertices.extend([(p[0], p[1]) for p in spline_points])
    return vertices

def extract_quantity_from_filename(filename):
    """
    Extracts the quantity from the filename.
    Expected format: 'name_quantity.dxf'.
    Returns 1 if quantity is not found.
    """
    base_name = os.path.splitext(filename)[0]
    parts = base_name.rsplit('_', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    else:
        return 1


def calculate_cut_points(doc, tolerance=1.0):
    """
    Calculate the number of cut points (entry points) for laser cutting.
    Only points on the external boundaries and standalone lines are considered.
    """
    msp = doc.modelspace()
    lines = []  # Список для хранения всех линий

    # Собираем все линии из всех объектов
    for entity in msp:
        try:
            if entity.dxftype() == 'LINE':
                start = (round(float(entity.dxf.start[0]) / tolerance) * tolerance,
                        round(float(entity.dxf.start[1]) / tolerance) * tolerance)
                end = (round(float(entity.dxf.end[0]) / tolerance) * tolerance,
                    round(float(entity.dxf.end[1]) / tolerance) * tolerance)
                lines.append((start, end))

            elif entity.dxftype() in ['LWPOLYLINE', 'POLYLINE']:
                points = list(entity.get_points())
                if points:
                    coords = [(round(float(p[0]) / tolerance) * tolerance,
                            round(float(p[1]) / tolerance) * tolerance) for p in points]
                    if entity.closed:
                        # Замкнутая полилиния
                        lines.append((coords[0], coords[-1]))
                    else:
                        # Незамкнутая полилиния
                        for i in range(len(coords) - 1):
                            lines.append((coords[i], coords[i + 1]))

            elif entity.dxftype() == 'CIRCLE':
                center = (round(float(entity.dxf.center[0]) / tolerance) * tolerance,
                        round(float(entity.dxf.center[1]) / tolerance) * tolerance)
                radius = float(entity.dxf.radius)
                # Аппроксимируем круг как замкнутую полилинию
                angles = [math.radians(t) for t in range(0, 360, 10)]
                coords = [(round((center[0] + radius * math.cos(a)) / tolerance) * tolerance,
                        round((center[1] + radius * math.sin(a)) / tolerance) * tolerance) for a in angles]
                lines.append((coords[0], coords[-1]))

            elif entity.dxftype() == 'ARC':
                center = (round(float(entity.dxf.center[0]) / tolerance) * tolerance,
                        round(float(entity.dxf.center[1]) / tolerance) * tolerance)
                radius = float(entity.dxf.radius)
                start_angle = math.radians(float(entity.dxf.start_angle))
                end_angle = math.radians(float(entity.dxf.end_angle))
                # Аппроксимируем дугу как набор точек
                angles = [start_angle + (end_angle - start_angle) * t / 100 for t in range(101)]
                coords = [(round((center[0] + radius * math.cos(a)) / tolerance) * tolerance,
                        round((center[1] + radius * math.sin(a)) / tolerance) * tolerance) for a in angles]
                lines.append((coords[0], coords[-1]))

            elif entity.dxftype() == 'SPLINE':
                # Аппроксимируем сплайн как набор точек
                spline_points = list(entity.flattening(distance=0.01))
                coords = [(round(float(p[0]) / tolerance) * tolerance,
                        round(float(p[1]) / tolerance) * tolerance) for p in spline_points]
                if entity.closed:
                    # Замкнутый сплайн
                    lines.append((coords[0], coords[-1]))
                else:
                    # Незамкнутый сплайн
                    lines.append((coords[0], coords[-1]))

        except Exception as e:
            print(f"Error processing {entity.dxftype()}: {e}")
            continue

    # Формируем граф связности
    graph = defaultdict(list)
    for line in lines:
        start, end = line
        graph[start].append(end)
        graph[end].append(start)

    # Функция для поиска контуров
    def find_contours(graph, start_point):
        contour = []
        stack = [start_point]
        while stack:
            point = stack.pop()
            if point not in contour:
                contour.append(point)
                for neighbor in graph[point]:
                    stack.append(neighbor)
        return contour

    # Находим все контуры
    contours = []
    visited = set()
    for point in graph:
        if point not in visited:
            contour = find_contours(graph, point)
            if len(contour) > 1:  # Игнорируем одиночные точки
                contours.append(contour)
                visited.update(contour)

    # Считаем точки врезки
    cut_points = set()
    for contour in contours:
        if len(contour) > 2:  # Замкнутые контуры
            # Добавляем только начальную точку контура
            cut_points.add(contour[0])
        else:  # Одиночные линии
            # Проверяем, связана ли линия с другими объектами
            if len(graph[contour[0]]) == 1:  # Линия не связана с другими объектами
                cut_points.add(contour[0])

    print(f"Total contours found: {len(contours)}")  # Отладка
    print(f"Total cut points found: {len(cut_points)}")  # Отладка
    return len(cut_points)  # Возвращаем количество уникальных точек врезки


def analyze_dxf(filepath):
    """Анализирует DXF файл и возвращает результаты"""
    try:
        doc = ezdxf.readfile(filepath)
        msp = doc.modelspace()
        
        if len(msp) == 0:
            return {"error": "Файл не содержит объектов"}
        
        results = {
            'filename': os.path.basename(filepath),
            'total_length': calculate_total_length(doc),
            'area': calculate_bounding_box_area(doc),
            'cut_points': calculate_cut_points(doc),
            'entity_count': len(list(msp))
        }
        return results
        
    except Exception as e:
        return {"error": f"Ошибка обработки файла: {str(e)}"}

@app.route('/', methods=['GET', 'POST'])
def upload_files():
    if request.method == 'POST':

        session.pop('calculation_results', None)

        if 'files' not in request.files:
            flash('Не выбраны файлы для загрузки', 'error')
            return redirect(request.url)
        
        files = request.files.getlist('files')
        material = request.form.get('material')
        try:
            thickness = float(request.form.get('thickness'))
        except (TypeError, ValueError):
            flash('Некорректное значение толщины', 'error')
            return redirect(request.url)
        
        results = []
        warning_shown = False
        total_cost = 0
        
        for file in files:
            if file.filename == '':
                continue
            
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                
                analysis = analyze_dxf(filepath)
                if 'error' not in analysis:
                    cost, warning = calculate_cutting_cost_with_warning(
                        material,
                        thickness,
                        analysis['total_length'],
                        analysis['cut_points']
                    )
                    
                    if warning and not warning_shown:
                        flash(warning, 'warning')
                        warning_shown = True
                    
                    analysis.update({
                        'material': material,
                        'thickness': thickness,
                        'cutting_cost': cost
                    })
                    results.append(analysis)
                    total_cost += cost
        
        if not results:
            return redirect(request.url)
        
        # Сохраняем результаты в сессию
        session['calculation_results'] = {
            'results': results,
            'total_cost': total_cost,
            'material': material,
            'thickness': thickness
        }
        
        return render_template('results.html', 
                            results=results,
                            total_cost=total_cost)
    
    # Проверяем есть ли сохраненные результаты
    if 'calculation_results' in session:
        data = session['calculation_results']
        return render_template('results.html',
                            results=data['results'],
                            total_cost=data['total_cost'])
    
    price_data = parse_price_table()
    return render_template('index.html', materials=price_data['materials'])

def send_application_email(name, phone, files_info, attachments):
    try:
        msg = MIMEMultipart()
        msg['Subject'] = f'Заявка на резку от {name}'
        msg['From'] = app.config['MAIL_DEFAULT_SENDER']
        msg['To'] = app.config['ADMIN_EMAIL']
        
        text = f"""<h2>Новая заявка</h2>
        <p><b>Имя:</b> {name}</p>
        <p><b>Телефон:</b> {phone}</p>"""
        
        for file in files_info:
            text += f"""<hr>
            <p><b>Файл:</b> {file['filename']}<br>
            <b>Материал:</b> {file['material']}<br>
            <b>Стоимость:</b> {file['cutting_cost']} руб.</p>"""
        
        msg.attach(MIMEText(text, 'html'))
        
        for filepath in attachments:
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(filepath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                    msg.attach(part)
        
        with smtplib.SMTP_SSL(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as server:
            server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
            server.send_message(msg)
        
        return True
    except Exception as e:
        print(f"Ошибка отправки: {str(e)}")
        return False


@app.route('/price')
def show_price():
    # Получаем сохраненные данные из сессии
    saved_data = session.get('calculation_results', None)
    
    price_data = parse_price_table()
    return render_template('price.html', 
                         materials=price_data['materials'],
                         prices=price_data['prices'],
                         thicknesses=price_data['sorted_thickness'],
                         saved_results=saved_data)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/submit_application', methods=['POST'])
def submit_application():
    try:
        if not request.is_json:
            return jsonify({'success': False, 'message': 'Invalid request format'}), 400
            
        data = request.get_json()
        name = data.get('name')
        phone = data.get('phone')
        files_info = data.get('files_info', [])
        
        if not name or not phone:
            return jsonify({'success': False, 'message': 'Пожалуйста, заполните все поля'}), 400
        
        filepaths = []
        for file_info in files_info:
            filename = secure_filename(file_info.get('filename', ''))
            if filename:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.exists(filepath):
                    filepaths.append(filepath)
        
        success = send_application_email(name, phone, files_info, filepaths)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Заявка успешно отправлена!'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Ошибка при отправке заявки'
            }), 500
            
    except Exception as e:
        app.logger.error(f"Error submitting application: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Произошла ошибка: {str(e)}'
        }), 500
    
@app.route('/clear-session')
def clear_session():
    session.pop('calculation_results', None)
    return redirect(url_for('upload_files'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('upload_files'))

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True)