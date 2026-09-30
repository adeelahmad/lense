import type { SessionTokens, SessionUser } from "@/lib/auth/tokens";

type SessionError = "RefreshTokenError";

declare module "next-auth" {
  /** What the Credentials provider's `authorize` returns. */
  interface User {
    admin?: boolean;
    tokens?: SessionTokens;
  }

  interface Session {
    /** Bearer token for the FastAPI API. */
    accessToken: string;
    user: SessionUser;
    /** Set when the backend session ended; the UI signs the user out. */
    error?: SessionError;
  }
}

// next-auth/jwt re-exports @auth/core/jwt; augment the source (pin @auth/core to next-auth's version).
declare module "@auth/core/jwt" {
  interface JWT extends SessionTokens {
    user: SessionUser;
    error?: SessionError;
  }
}
