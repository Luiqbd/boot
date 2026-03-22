package com.example.loginapp

import android.content.Context
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.loginapp.adapter.MessageAdapter
import com.example.loginapp.databinding.ActivityChatBinding
import com.example.loginapp.model.Message
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken

class ChatActivity : AppCompatActivity() {

    private lateinit var binding: ActivityChatBinding
    private lateinit var messageAdapter: MessageAdapter
    private val sharedPrefs by lazy { getSharedPreferences("LoginApp", Context.MODE_PRIVATE) }

    private var chatId: String = ""
    private var chatName: String = ""

    companion object {
        private const val KEY_MESSAGES = "messages_"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Obter dados do intent com valores padrão
        chatId = intent.getStringExtra("chat_id") ?: run {
            Toast.makeText(this, "Erro: ID do chat não encontrado", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        chatName = intent.getStringExtra("chat_name") ?: "Chat"

        setupToolbar()
        setupRecyclerView()
        setupMessageInput()
        loadMessages()
    }

    private fun setupToolbar() {
        setSupportActionBar(binding.toolbar)
        supportActionBar?.title = chatName
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
    }

    private fun setupRecyclerView() {
        val currentUserId = sharedPrefs.getString("logged_user", "") ?: ""
        messageAdapter = MessageAdapter(currentUserId)

        binding.rvMessages.apply {
            layoutManager = LinearLayoutManager(this@ChatActivity).apply {
                stackFromEnd = true
            }
            adapter = messageAdapter
        }
    }

    private fun setupMessageInput() {
        binding.btnSend.setOnClickListener {
            val messageText = binding.etMessage.text.toString().trim()
            if (messageText.isNotEmpty()) {
                sendMessage(messageText)
            }
        }
    }

    private fun loadMessages() {
        try {
            val currentUser = sharedPrefs.getString("logged_user", "") ?: ""
            if (currentUser.isEmpty()) {
                Toast.makeText(this, "Erro: Usuário não identificado", Toast.LENGTH_SHORT).show()
                return
            }
            
            val prefs = getSharedPreferences("users_$currentUser", Context.MODE_PRIVATE)
            val messagesJson = prefs.getString(KEY_MESSAGES + chatId, null)

            val messages = if (messagesJson != null) {
                try {
                    val type = object : TypeToken<List<Message>>() {}.type
                    Gson().fromJson<List<Message>>(messagesJson, type) ?: getSampleMessages(currentUser)
                } catch (e: Exception) {
                    getSampleMessages(currentUser)
                }
            } else {
                getSampleMessages(currentUser)
            }

            messageAdapter.submitList(messages)
            binding.rvMessages.scrollToPosition(messages.size - 1)
        } catch (e: Exception) {
            Toast.makeText(this, "Erro ao carregar mensagens: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun getSampleMessages(currentUser: String): List<Message> {
        val sampleMessages = listOf(
            Message(
                id = "1",
                senderId = chatId,
                senderName = chatName,
                receiverId = currentUser,
                message = "Olá! 👋",
                timestamp = System.currentTimeMillis() - 3600000,
                isFromMe = false
            ),
            Message(
                id = "2",
                senderId = currentUser,
                senderName = sharedPrefs.getString("logged_user_name", currentUser) ?: currentUser,
                receiverId = chatId,
                message = "Oi! Tudo bem?",
                timestamp = System.currentTimeMillis() - 3500000,
                isFromMe = true
            ),
            Message(
                id = "3",
                senderId = chatId,
                senderName = chatName,
                receiverId = currentUser,
                message = "Estou bem! E você?",
                timestamp = System.currentTimeMillis() - 1800000,
                isFromMe = false
            )
        )
        
        val prefs = getSharedPreferences("users_$currentUser", Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_MESSAGES + chatId, Gson().toJson(sampleMessages)).apply()
        
        return sampleMessages
    }

    private fun sendMessage(messageText: String) {
        val currentUser = sharedPrefs.getString("logged_user", "") ?: ""
        val currentUserName = sharedPrefs.getString("logged_user_name", currentUser) ?: currentUser

        val newMessage = Message(
            id = System.currentTimeMillis().toString(),
            senderId = currentUser,
            senderName = currentUserName,
            receiverId = chatId,
            message = messageText,
            timestamp = System.currentTimeMillis(),
            isFromMe = true
        )

        // Salvar mensagem
        val prefs = getSharedPreferences("users_$currentUser", Context.MODE_PRIVATE)
        val messagesJson = prefs.getString(KEY_MESSAGES + chatId, null)
        val messages = if (messagesJson != null) {
            val type = object : TypeToken<MutableList<Message>>() {}.type
            Gson().fromJson<MutableList<Message>>(messagesJson, type)
        } else {
            mutableListOf()
        }

        messages.add(newMessage)
        prefs.edit().putString(KEY_MESSAGES + chatId, Gson().toJson(messages)).apply()

        // Atualizar UI
        val currentList = messageAdapter.currentList.toMutableList()
        currentList.add(newMessage)
        messageAdapter.submitList(currentList)

        binding.etMessage.text?.clear()
        binding.rvMessages.scrollToPosition(currentList.size - 1)

        // Simular resposta automática após 2 segundos
        simulateReply()
    }

    private fun simulateReply() {
        binding.root.postDelayed({
            val replies = listOf(
                "Entendi! 👍",
                "Que legal! 😄",
                "Pode me contar mais?",
                "Ok, sem problemas!",
                "Entendido!",
                "Haha, muito bom! 😂",
                "Vamos lá! 🚀",
                "Perfeito! ✨"
            )
            val randomReply = replies.random()

            val currentUser = sharedPrefs.getString("logged_user", "") ?: ""
            val replyMessage = Message(
                id = System.currentTimeMillis().toString(),
                senderId = chatId,
                senderName = chatName,
                receiverId = currentUser,
                message = randomReply,
                timestamp = System.currentTimeMillis(),
                isFromMe = false
            )

            val prefs = getSharedPreferences("users_$currentUser", Context.MODE_PRIVATE)
            val messagesJson = prefs.getString(KEY_MESSAGES + chatId, null)
            val messages = if (messagesJson != null) {
                val type = object : TypeToken<MutableList<Message>>() {}.type
                Gson().fromJson<MutableList<Message>>(messagesJson, type)
            } else {
                mutableListOf()
            }
            messages.add(replyMessage)
            prefs.edit().putString(KEY_MESSAGES + chatId, Gson().toJson(messages)).apply()

            val currentList = messageAdapter.currentList.toMutableList()
            currentList.add(replyMessage)
            messageAdapter.submitList(currentList)

            binding.rvMessages.scrollToPosition(currentList.size - 1)
        }, 2000)
    }

    override fun onCreateOptionsMenu(menu: Menu?): Boolean {
        menuInflater.inflate(R.menu.menu_chat, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            android.R.id.home -> {
                finish()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
}
