import "server-only";

import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  NotAuthorizedException,
  UserNotFoundException,
} from "@aws-sdk/client-cognito-identity-provider";

import { settings } from "./config";

export type Tokens = { access: string; id: string; refresh?: string };

export class SignInError extends Error {}

let client: CognitoIdentityProviderClient | null = null;

function cognito(): CognitoIdentityProviderClient {
  // Sign-in and refresh are public calls with the app client id. The SDK sends them
  // unsigned, so no AWS credentials are involved. Timeouts so an unreachable Cognito
  // fails sign-in with a message instead of hanging.
  client ??= new CognitoIdentityProviderClient({
    region: settings.region(),
    requestHandler: { connectionTimeout: 5_000, requestTimeout: 15_000 },
  });
  return client;
}

export async function signIn(username: string, password: string): Promise<Tokens> {
  try {
    const out = await cognito().send(
      new InitiateAuthCommand({
        ClientId: settings.clientId(),
        AuthFlow: "USER_PASSWORD_AUTH",
        AuthParameters: { USERNAME: username, PASSWORD: password },
      }),
    );
    const r = out.AuthenticationResult;
    if (!r?.AccessToken || !r.IdToken) {
      throw new SignInError(
        out.ChallengeName
          ? "This account needs a step the console doesn't support yet; ask an admin."
          : "Sign-in failed.",
      );
    }
    return { access: r.AccessToken, id: r.IdToken, refresh: r.RefreshToken };
  } catch (err) {
    if (err instanceof NotAuthorizedException || err instanceof UserNotFoundException) {
      throw new SignInError("Wrong username or password.");
    }
    throw err;
  }
}

export async function refresh(refreshToken: string): Promise<Tokens | null> {
  try {
    const out = await cognito().send(
      new InitiateAuthCommand({
        ClientId: settings.clientId(),
        AuthFlow: "REFRESH_TOKEN_AUTH",
        AuthParameters: { REFRESH_TOKEN: refreshToken },
      }),
    );
    const r = out.AuthenticationResult;
    return r?.AccessToken && r.IdToken ? { access: r.AccessToken, id: r.IdToken } : null;
  } catch (err) {
    if (err instanceof NotAuthorizedException) return null; // revoked or expired
    throw err;
  }
}
