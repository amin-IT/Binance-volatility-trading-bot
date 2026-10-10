# signals/

Signal modules and the bot talk through files in this folder. They are created while
the bot runs and removed when it starts, so this folder is otherwise empty.

| File                         | Meaning                                                              |
|------------------------------|----------------------------------------------------------------------|
| `<name>.exs`, `<name>.buy`   | one pair per line (e.g. `BTCUSDT`) for the bot to buy                |
| `<name>.sell`                | one pair per line for the bot to sell if it holds it                 |
| `<name>.pause`, `paused.exc` | while any exists the bot buys nothing (stop loss / take profit keep working) |

Buy and sell files are deleted once the bot has read them.
