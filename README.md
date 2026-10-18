# CrewOps

A voice-first agent for airline crew control. A controller asks, by voice or text, who can
cover a sick crew member, whether a replacement is legal, or what a disruption will cost,
and gets an answer that is legally checked, ranked by cost and cited. Every crew id, duty
hour and figure comes from a deterministic rules engine, and a verifier checks each answer
against the tool results before anyone hears it.

Its model layer is NVIDIA Llama-3.1-Nemotron-70B-Instruct, served by Nebius Token Factory;
its backend runs on AWS. Built for the
[Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/).

Development happens on the `dev` branch.

## License

[MIT](LICENSE)
