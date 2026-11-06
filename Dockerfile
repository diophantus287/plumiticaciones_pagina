FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html actividades.json plumiticaciones.png plumi_acti.png /usr/share/nginx/html/
COPY imagenes /usr/share/nginx/html/imagenes
COPY static /usr/share/nginx/html/static
