# 🟢🔵 Blue-Green Deployment with Docker Compose and Nginx

## Overview

This project demonstrates a **Blue-Green deployment strategy** using **Docker Compose** and **Nginx** as the load balancer.  
The setup ensures **zero downtime deployments** by running two identical Node.js services — **Blue** (active) and **Green** (standby).  
When the active environment becomes unavailable, Nginx automatically redirects traffic to the healthy environment.

---

## Architecture

The environment consists of:

- **Nginx** – Routes traffic to the active pool and manages automatic failover.
- **Blue Service** – Active Node.js container.
- **Green Service** – Backup Node.js container (activated during failover).
- **Docker Compose** – Orchestrates all containers and environment variables.

Traffic flow:
Client → Nginx → Blue (Active)
Green (Backup during Blue failure)

---

## Features

- Blue/Green containerized environments for zero-downtime deployment
- Health checks and simulated failures via `/chaos` endpoints
- Automatic failover between Blue and Green
- Configurable environment variables via `.env`
- Header forwarding (`X-App-Pool`, `X-Release-Id`) preserved through Nginx

---

## Project Files

.
├── docker-compose.yml # Service definitions
├── nginx/nginx.conf # Nginx load balancing configuration
├── .env.example # Example environment variables
├── README.md # Project documentation
└── DECISION.md (optional) # Implementation notes or reasoning

---

## Environment Variables

You can configure deployment behavior using the `.env` file.

Example:
BLUE_IMAGE=yimikaade/wonderful:devops-stage-two
GREEN_IMAGE=yimikaade/wonderful:devops-stage-two
ACTIVE_POOL=blue
RELEASE_ID_BLUE=v1.0.0
RELEASE_ID_GREEN=v1.0.0
PORT=8000
NGINX_PUBLIC_PORT=8080
BLUE_DIRECT_PORT=8081
GREEN_DIRECT_PORT=8082

Deployment Steps
Clone the repository

git clone https://github.com/chideracloud/blue_green_deployment.git
cd blue_green_deployment
Create a .env file

cp .env.example .env
Then update the variables as needed.

Run Docker Compose

docker compose up -d
Access the application

http://localhost:8080
Trigger failover test
Simulate a failure in Blue:

curl -X POST http://localhost:8081/chaos/start?mode=error
Then check that requests to:

http://localhost:8080/version
return responses from Green.

Switching Pools Manually
To manually toggle the active environment:

Open .env

ACTIVE_POOL=green

Apply changes:

docker compose up -d

Task Command
Start services docker compose up -d
Stop services docker compose down
View logs docker compose logs -f nginx
Restart Nginx docker exec -it nginx nginx -s reload

## Author

Chidera cloud

Blue-Green Deployment ensures fast rollouts, seamless failover, and zero downtime
