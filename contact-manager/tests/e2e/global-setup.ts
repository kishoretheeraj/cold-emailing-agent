import type { Server } from "node:http";
import { startStubServer } from "./stub-server";

let server: Server;

export default async function globalSetup() {
  server = await startStubServer();
  return async () => {
    server.close();
  };
}
