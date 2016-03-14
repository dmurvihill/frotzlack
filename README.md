# Frotzlack
Frotzlack is a Slack interface for the [Frotz](https://github.com/DavidGriffith/frotz) Z-machine emulator.

Frotzlack reduces office productivity by encouraging employees to spend the
majority of their time playing Zork.

## Quickstart
1. [Create a slackbot](https://everquote.slack.com/apps/build/custom-integration) to provide the game interface.
2. Rename frotzlack.conf.example to frotzlack.conf, and add your Slackbot's
  API token and username. Consider editing the admin username as well.
3. `vagrant up`
Now add the bot to a channel and say '@zork play' (if your bot's name is
'zork'), and watch the magic unfold.

## Known Issues
* Graceful shutdown is not working. You will have to kill the process.
* Occasionally, the bot will miss some output from Frotz. If this happens in
  combat it may be fatal.
* No consideration for what happens if a Frotz process dies for whatever reason
* Vagrant setup process downloads code and files from various unstable places
  around the web without any file integrity or version checking.

## Contributing
This package is maintained by Dolan Murvihill in Cambridge, MA. He can be
reached at \[first initial\]\[last name\] at GMail.

To get your change merged:

* write unit tests
* follow PEP-8
* open a pull request
