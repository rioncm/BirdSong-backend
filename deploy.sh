cd birdsong-backend
git pull origin main
docker build -t birdsong-be:0.0.0 .

cd ../birdsong-frontend
git pull origin main
docker build -t birdsong-fe:0.0.0 .

cd ../
docker compose down
docker compose up -d
