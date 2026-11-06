import hmac, json, os, re, unicodedata
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urlparse
from flask import Flask, Response, jsonify, request, send_file

DATA = "/data"
JSON_FILE = f"{DATA}/actividades.json"
IMG_DIR = f"{DATA}/imagenes"
PAGES_DIR = f"{DATA}/actividades"
PDFS_DIR = f"{PAGES_DIR}/pdfs"
MARK = "<!-- generado-por-admin -->"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
USER = os.environ.get("ADMIN_USER", "admin")
FIELDS = ("sentido", "proceso", "curso", "material")
EXTS = {"png", "jpg", "jpeg", "webp", "gif", "svg"}
CONTENT_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "a"}


class ContentSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag not in CONTENT_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href", "").strip()
            scheme = urlparse(href).scheme.lower()
            if href and (not scheme or scheme in {"http", "https", "mailto"}):
                self.parts.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
                return
            self.parts.append("<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in CONTENT_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(escape(data))


def sanitize_content(value):
    parser = ContentSanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


@app.before_request
def auth():
    a = request.authorization
    ok = (
        PASSWORD
        and a
        and hmac.compare_digest(a.username or "", USER)
        and hmac.compare_digest(a.password or "", PASSWORD)
    )
    if not ok:
        return Response("Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="Mates admin"'})


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "actividad"


def load():
    with open(JSON_FILE, encoding="utf-8") as f:
        return json.load(f)


def save(items):
    # Escritura in situ: el archivo está montado como volumen individual
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def page_name(enlace):
    if isinstance(enlace, str) and re.fullmatch(r"actividades/[a-z0-9._-]+\.html", enlace):
        return os.path.basename(enlace)
    return None


def purge_orphan_pages(items):
    referenced = {name for name in (page_name(item.get("enlace", "")) for item in items) if name}
    removed = []
    for entry in os.scandir(PAGES_DIR):
        if (
            not re.fullmatch(r"[a-z0-9._-]+\.html", entry.name)
            or entry.name in referenced
            or not entry.is_file(follow_symlinks=False)
        ):
            continue
        with open(entry.path, encoding="utf-8") as f:
            if MARK not in f.read():
                continue
        os.remove(entry.path)
        removed.append(entry.name)
    return removed


def purge_activity_image(image, items):
    if not isinstance(image, str) or not re.fullmatch(r"imagenes/[a-z0-9._-]+\.(?:png|jpg|jpeg|webp|gif|svg)", image):
        return False
    if os.path.basename(image) == "placeholder.svg":
        return False
    if any(item.get("imagen") == image for item in items):
        return False

    filename = os.path.basename(image)
    for root, dirs, files in os.walk(PAGES_DIR, followlinks=False):
        dirs[:] = [directory for directory in dirs if not os.path.islink(os.path.join(root, directory))]
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            with open(path, encoding="utf-8") as page:
                if filename in page.read():
                    return False

    path = os.path.join(IMG_DIR, filename)
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    os.remove(path)
    return True


def clean(data):
    content_html = sanitize_content(str(data.get("contenido_html", "")))
    item = {
        "titulo": str(data.get("titulo", "")).strip(),
        "descripcion": str(data.get("descripcion", "")).strip(),
        "contenido_html": content_html,
    }
    for k in FIELDS:
        vals = data.get(k, [])
        if isinstance(vals, str):
            vals = vals.split(",")
        item[k] = [v.strip() for v in vals if v.strip()]
    item["imagen"] = str(data.get("imagen", "")).strip() or "imagenes/placeholder.svg"
    item["enlace"] = str(data.get("enlace", "")).strip()
    item["pdf"] = str(data.get("pdf", "")).strip()
    item["contenido"] = str(data.get("contenido", "")).strip()
    item["referencia_web"] = str(data.get("referencia_web", "")).strip()
    return item


def write_page(item):
    content = "\n".join(
        f"  <p>{escape(p).replace(chr(10), '<br>')}</p>"
        for p in re.split(r"\n\s*\n", item["contenido"])
        if p.strip()
    )
    img = ""
    if item["imagen"] and "placeholder" not in item["imagen"]:
        img = f'  <img class="foto" src="../{escape(item["imagen"], quote=True)}" alt="{escape(item["titulo"], quote=True)}">\n'
    description = f"<p><em>{escape(item['descripcion'])}</em></p>"
    tags = ""
    for k, label in (("curso", "Curso"), ("material", "Material"), ("proceso", "Proceso"), ("sentido", "Sentido")):
        links = " ".join(
            f'<a class="tag" href="../index.html?{k}={quote(t)}">{escape(t)}</a>'
            for t in item.get(k, [])
        )
        if links:
            tags += f"    <p><strong>{label}:</strong> {links}</p>\n"
    tags = f'  <div class="tags">\n{tags}  </div>\n' if tags else ""
    pdf = ""
    if item["pdf"]:
        pdf = f'  <p><a class="pdf" href="../{escape(item["pdf"], quote=True)}" target="_blank">&#128196; Documentación adicional (PDF)</a></p>\n'
    reference = ""
    if item["referencia_web"]:
        url = escape(item["referencia_web"], quote=True)
        reference = f'  <p><a class="web-reference" href="{url}" target="_blank" rel="noopener noreferrer" title="{url}">Referencia web</a></p>\n'
    html = f"""<!DOCTYPE html>
{MARK}
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(item['titulo'])} - Plumiticaciones</title>
  <link rel="icon" href="../static/plumi_favicon.ico" sizes="any">
  <style>
    html, body {{ background: #EFEFF3; }}
    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 1rem; color: #1f2937; line-height: 1.6; }}
    p, li {{ text-align: justify; }}
    img.banner {{ display: block; width: 45%; min-width: 180px; margin: 0 auto 1rem; }}
    @media (max-width: 779px) {{
      img.banner {{ width: calc(90% + 1.8rem); min-width: 0; margin-left: calc(5% - .9rem); margin-right: calc(5% - .9rem); }}
    }}
    img.foto {{ display: block; max-width: 100%; max-height: 420px; margin: 1rem auto; border-radius: .75rem; box-shadow: 0 4px 12px rgba(31, 41, 55, .12); }}
    a {{ color: #2563eb; }}
    .tags p {{ margin: .3rem 0; text-align: left; }}
    a.tag {{ display: inline-block; background: #dbeafe; color: #1e40af; border-radius: 999px; padding: .1rem .6rem; font-size: .85rem; text-decoration: none; }}
    a.tag:hover, a.tag:focus-visible {{ background: #bfdbfe; outline: none; }}
    .home-button {{ display: table; margin: 1.5rem auto 0; padding: .6rem 1.1rem; border-radius: 999px; background: #2563eb; color: #fff; font-weight: bold; text-decoration: none; transition: background-color .2s; }}
    .home-button:hover, .home-button:focus-visible {{ background: #1d4ed8; outline: none; }}
    .web-reference {{ display: inline-block; padding: .4rem .7rem; border: 1px solid #2563eb; border-radius: .4rem; font-weight: bold; text-decoration: none; transition: background-color .2s, color .2s; }}
    .web-reference:hover, .web-reference:focus-visible {{ background: #2563eb; color: #fff; outline: none; }}
  </style>
</head>
<body>
  <a href="../index.html" aria-label="Ir al inicio"><img class="banner" src="../static/plumi_banner.jpg" alt="Plumiticaciones"></a>
  <h1>{escape(item['titulo'])}</h1>
{description}
{img}{item["contenido_html"] or content}
{reference}
{pdf}
{tags}  <a class="home-button" href="../index.html">Inicio</a>
</body>
</html>
"""
    name = os.path.basename(item["enlace"])
    path = f"{PAGES_DIR}/{name}"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if MARK not in f.read():
                return  # página hecha a mano: no se sobrescribe
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def prepare(data):
    item = clean(data)
    if not item["titulo"]:
        return None, "El título es obligatorio"
    if item["pdf"] and not re.fullmatch(r"actividades/pdfs/[a-z0-9._-]+\.pdf", item["pdf"]):
        return None, "PDF no válido"
    if item["referencia_web"]:
        parsed = urlparse(item["referencia_web"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None, "La referencia web debe ser una URL http o https válida"
    if not item["enlace"] and (item["contenido"] or item["pdf"]):
        item["enlace"] = f"actividades/{slugify(item['titulo'])}.html"
    if re.fullmatch(r"actividades/[a-z0-9._-]+\.html", item["enlace"]):
        write_page(item)
    return item, None


@app.get("/admin/")
def panel():
    return send_file("panel.html")


@app.get("/admin/api/actividades")
def listar():
    return jsonify(load())


@app.get("/admin/api/imagenes")
def imagenes():
    return jsonify(sorted(f"imagenes/{f}" for f in os.listdir(IMG_DIR) if not f.startswith(".")))


@app.post("/admin/api/actividades")
def crear():
    item, err = prepare(request.get_json(force=True))
    if err:
        return jsonify(error=err), 400
    items = load()
    items.append(item)
    save(items)
    return jsonify(item)


@app.put("/admin/api/actividades/<int:i>")
def editar(i):
    items = load()
    if not 0 <= i < len(items):
        return jsonify(error="No existe"), 404
    item, err = prepare(request.get_json(force=True))
    if err:
        return jsonify(error=err), 400
    items[i] = item
    save(items)
    return jsonify(item)


@app.delete("/admin/api/actividades/<int:i>")
def borrar(i):
    items = load()
    if not 0 <= i < len(items):
        return jsonify(error="No existe"), 404
    deleted = items.pop(i)
    save(items)
    purge_orphan_pages(items)
    image_deleted = purge_activity_image(deleted.get("imagen"), items)
    return jsonify(ok=True, imagen_eliminada=image_deleted)


@app.delete("/admin/api/paginas-huerfanas")
def purgar_paginas_huerfanas():
    removed = purge_orphan_pages(load())
    return jsonify(eliminadas=removed)


@app.post("/admin/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Sin archivo"), 400
    base, _, ext = f.filename.rpartition(".")
    ext = ext.lower()
    if ext not in EXTS:
        return jsonify(error="Formato no permitido"), 400
    name = f"{slugify(base)}.{ext}"
    f.save(f"{IMG_DIR}/{name}")
    return jsonify(path=f"imagenes/{name}")


@app.post("/admin/api/upload-pdf")
def upload_pdf():
    f = request.files.get("file")
    if not f or not f.filename or not f.filename.lower().endswith(".pdf"):
        return jsonify(error="Solo se admiten archivos PDF"), 400
    if f.read(5) != b"%PDF-":
        return jsonify(error="El archivo no es un PDF válido"), 400
    f.seek(0)
    name = f"{slugify(f.filename[:-4])}.pdf"
    os.makedirs(PDFS_DIR, exist_ok=True)
    f.save(f"{PDFS_DIR}/{name}")
    return jsonify(path=f"actividades/pdfs/{name}")
