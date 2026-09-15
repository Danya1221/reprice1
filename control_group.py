"""Small control extension: bind the finished price to the current Telegram group."""

from control_botapi import BotAPIController


class GroupBindingController(BotAPIController):
    async def handle_message(self, message):
        text = (message.get("text") or "").strip()
        if text:
            command = text.split(maxsplit=1)[0].lower().split("@", 1)[0]
            chat = message.get("chat") or {}
            chat_type = chat.get("type", "private")
            user_id = message.get("from", {}).get("id")
            chat_id = chat.get("id")

            if command in {"/bind", "/group"}:
                if user_id not in self.admins:
                    await self.send(chat_id, f"Нет доступа. Твой Telegram ID: {user_id}")
                    return

                if chat_type not in {"group", "supergroup"}:
                    await self.send(
                        chat_id,
                        "Добавь этого управляющего бота в нужную группу и отправь /bind прямо в группе."
                    )
                    return

                publisher = self.service.publisher
                if not hasattr(publisher, "bind_group"):
                    await self.send(chat_id, "Эта версия ещё не умеет привязывать группу")
                    return

                try:
                    await publisher.bind_group(
                        chat_id,
                        title=chat.get("title") or "",
                        chat_type=chat_type,
                    )
                except Exception as exc:
                    await self.send(chat_id, "Не удалось привязать группу: " + str(exc))
                    return

                self.service.state.set("last_result", f"Группа публикации привязана: {chat.get('title') or chat_id}")
                await self.send(
                    chat_id,
                    "✅ Группа привязана. Теперь прайс будет публиковаться и обновляться здесь."
                )
                return

        await super().handle_message(message)
