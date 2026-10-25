// Inventory API (demo). Connects to Postgres using the standard PG* environment variables
// (PGHOST, PGDATABASE, PGUSER, PGPASSWORD), which node-postgres reads natively.
const { Client } = require("pg");

const client = new Client();

console.log("inventory-api starting, connecting to", process.env.PGHOST);
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
