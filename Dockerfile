# Usamos una imagen oficial y ligera de Python
FROM python:3.11-slim

# Evita archivos temporales .pyc y muestra logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Carpeta de trabajo dentro del contenedor
WORKDIR /app

# Instalamos dependencias primero (optimiza caché de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el código de tu microservicio
COPY main.py .

# Exponemos el puerto 5001
EXPOSE 5001

# Arrancamos el servidor abierto a toda la red (0.0.0.0)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
