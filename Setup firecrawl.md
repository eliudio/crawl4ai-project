git clone https://github.com/mendableai/firecrawl.git C:\src\firecrawl
cd C:\src\firecrawl
npm install
Create a .env file in the root of C:\src\firecrawl (copy from .env.example if available).Set necessary variables, such as database connections (e.g., DATABASE_URL for PostgreSQL), queue settings (RabbitMQ if used), and any API keys for external services.
Refer to the SELF_HOST.md file in the repo for detailed env var requirements. For basic scraping, focus on core DB and server settings
cd C:\src\firecrawl
docker-compose up