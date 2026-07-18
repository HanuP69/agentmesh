FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY nginx-edge.conf /etc/nginx/nginx-edge.conf
COPY certs/ /etc/nginx/certs/
EXPOSE 443 80 8080
