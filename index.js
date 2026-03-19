const express = require("express");
const { Resend } = require("resend");

const app = express();
const resend = new Resend(process.env.RESEND_API_KEY);

app.get("/kroger/callback", async (req, res) => {
  const code = req.query.code;

  if (!code) {
    return res.send("<html><head><meta name='robots' content='noindex'></head><body>Nothing to see here.</body></html>");
  }

  try {
    await resend.emails.send({
      from: "OAuth Callback <onboarding@resend.dev>",
      to: "sam@oliverstreetcreative.com",
      subject: "Kroger Auth Code",
      text: `Your Kroger authorization code:\n\n${code}\n\nThis code is single-use and expires in ~30 seconds. Paste it to Studio Manager.`,
    });
  } catch (err) {
    console.error("Resend error:", err.message);
  }

  res.send("<html><head><meta name='robots' content='noindex'></head><body><h2>✅ Check your email.</h2></body></html>");
});

app.get("/", (req, res) => {
  res.send("<html><head><meta name='robots' content='noindex'></head><body>Nothing to see here.</body></html>");
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Listening on ${PORT}`));
