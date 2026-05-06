FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1
EXPOSE 8788
CMD ["python", "-m", "apps.control_plane.api"]
