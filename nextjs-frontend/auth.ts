import NextAuth, { CredentialsSignin } from "next-auth";
import Credentials from "next-auth/providers/credentials";

import { Auth } from "@/app/openapi-client";
import { createApiClient } from "@/lib/api/client";
import { endBackendSession, needsRefresh, refreshTokens, sessionUser, tokensFromPair } from "@/lib/auth/tokens";
import { loginSchema } from "@/lib/definitions";

/** Too many sign-in attempts (backend answered 429). */
export class ThrottledSignin extends CredentialsSignin {
  code = "throttled";
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  providers: [
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const parsed = loginSchema.safeParse(credentials);
        if (!parsed.success) return null;

        const { data, response } = await Auth.login({
          client: createApiClient(),
          body: parsed.data,
        });
        if (response?.status === 429) throw new ThrottledSignin();
        if (!data) return null;

        return { ...sessionUser(data.user), tokens: tokensFromPair(data) };
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      // Just signed in.
      if (user?.tokens && user.id && user.email) {
        return {
          ...token,
          ...user.tokens,
          user: {
            id: user.id,
            email: user.email,
            name: user.name ?? null,
            admin: Boolean(user.admin),
          },
          error: undefined,
        };
      }
      if (token.error || !needsRefresh(token.expiresAt)) return token;

      try {
        const pair = await refreshTokens(token.refreshToken);
        if (!pair) return { ...token, error: "RefreshTokenError" as const };
        return {
          ...token,
          ...tokensFromPair(pair),
          user: sessionUser(pair.user),
          error: undefined,
        };
      } catch (err) {
        // Backend unreachable or erroring: keep the session and retry on the next request.
        console.warn("Token refresh failed:", err);
        return token;
      }
    },
    session({ session, token }) {
      return {
        expires: session.expires,
        accessToken: token.accessToken,
        user: token.user,
        error: token.error,
      };
    },
  },
  events: {
    async signOut(message) {
      if ("token" in message && message.token?.refreshToken) {
        await endBackendSession(message.token.refreshToken);
      }
    },
  },
});
