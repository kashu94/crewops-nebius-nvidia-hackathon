"""Sign-in: a Cognito user pool with one group per role.

Nobody signs themselves up. An admin (here, the team command) creates every account, so the
pool holds exactly the people the operation knows about. Users sign in with a username; no
email or phone is stored, and nothing is ever sent. A forgotten password is reset by an
admin.

Roles are the groups, which only an admin can change. What else decides access (a crew
member's own crew id, a controller's desk) is kept out of Cognito altogether, in a user
profile in the operations table that only the team command writes: a Cognito attribute is,
by default, something users can edit about themselves. The app client can read the display
name and write only the user's locale.

The Lite feature plan has no monthly charge for this number of users.

If ``CREWOPS_ALEXA_REDIRECT_URLS`` is set we also add a hosted sign-in page and a second app
client just for Alexa account linking (authorization-code flow, Alexa's redirect URLs). A
person signs in to CrewOps once from the Alexa app, and after that the skill gets their
access token and the same role access they have anywhere else. The web client keeps direct
sign-in with no hosted page.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from crewops.config import Settings
from crewops.domain import Role
from crewops_infra.data_stack import DISPOSABLE_STAGES

GROUP_ORDER = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.APPROVER,
    Role.CONTROLLER,
    Role.AUDITOR,
    Role.REPORTER,
    Role.CREW,
    Role.SPEAKER,
)
"""Cognito group precedence, highest first. Roles add up in the application; the order only
decides which group's IAM role Cognito would hand out, and none is set."""


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, settings: Settings, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        disposable = settings.stage in DISPOSABLE_STAGES

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=settings.user_pool_name,
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True),
            sign_in_case_sensitive=False,
            account_recovery=cognito.AccountRecovery.NONE,
            feature_plan=cognito.FeaturePlan.LITE,
            mfa=cognito.Mfa.OFF,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
                temp_password_validity=Duration.days(1),
            ),
            deletion_protection=not disposable,
            removal_policy=RemovalPolicy.DESTROY if disposable else RemovalPolicy.RETAIN,
        )

        for precedence, role in enumerate(GROUP_ORDER):
            cognito.CfnUserPoolGroup(
                self,
                f"Group{role.value.title().replace('_', '')}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=role.value,
                precedence=precedence,
                description=f"The {role.value.replace('_', ' ')} role",
            )

        self.client = self.user_pool.add_client(
            "WebClient",
            user_pool_client_name=f"{settings.prefix}-web",
            generate_secret=False,
            # Direct sign-in only: no hosted login page, so no OAuth flows or callback URLs.
            disable_o_auth=True,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
            id_token_validity=Duration.hours(1),
            access_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(1),
            read_attributes=cognito.ClientAttributes().with_standard_attributes(fullname=True),
            # A client with no write list may write every standard attribute, so it lists one
            # harmless attribute instead: users cannot store a name, email or phone number.
            write_attributes=cognito.ClientAttributes().with_standard_attributes(locale=True),
        )

        self.alexa_client: cognito.UserPoolClient | None = None
        if settings.alexa_redirect_urls:
            domain = self.user_pool.add_domain(
                "AlexaSignIn",
                cognito_domain=cognito.CognitoDomainOptions(
                    # Unique worldwide, so the account id is part of it.
                    domain_prefix=f"{settings.prefix}-{self.account}"
                ),
            )
            self.alexa_client = self.user_pool.add_client(
                "AlexaClient",
                user_pool_client_name=f"{settings.prefix}-alexa",
                generate_secret=True,  # Alexa keeps it, entered once on the skill's linking page
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
                    callback_urls=list(settings.alexa_redirect_urls),
                ),
                supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
                prevent_user_existence_errors=True,
                enable_token_revocation=True,
                access_token_validity=Duration.hours(1),
                id_token_validity=Duration.hours(1),
                # Alexa renews the access token with this, so a link lasts a month of use.
                refresh_token_validity=Duration.days(30),
                read_attributes=cognito.ClientAttributes().with_standard_attributes(fullname=True),
                write_attributes=cognito.ClientAttributes().with_standard_attributes(locale=True),
            )
            CfnOutput(self, "AlexaClientId", value=self.alexa_client.user_pool_client_id)
            CfnOutput(self, "AlexaAuthorizeUri", value=f"{domain.base_url()}/oauth2/authorize")
            CfnOutput(self, "AlexaAccessTokenUri", value=f"{domain.base_url()}/oauth2/token")

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.client.user_pool_client_id)
