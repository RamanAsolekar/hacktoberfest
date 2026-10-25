// Inventory API (demo). Connects to Postgres and keeps a heartbeat query running.
const { Client } = require("pg");

const client = new Client({
  host: process.env.DB_HOST || "db",
  database: process.env.DB_NAME || "inventory",
  user: process.env.DB_USER || "postgres",
  password: process.env.DB_PASSWORD,
});

console.log("inventory-api starting, connecting to", client.host);
client
  .connect()
  .then(() => {
    console.log("connected to postgres; inventory-api ready");
    setInterval(() => client.query("SELECT 1"), 5000);
  })
  .catch((err) => {
    console.error("Error: " + err.message);
    process.exit(1);
  });
