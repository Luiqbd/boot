package com.example.loginapp.model

import java.io.Serializable

data class Message(
    val id: String = "",
    val senderId: String = "",
    val senderName: String = "",
    val receiverId: String = "",
    val message: String = "",
    val timestamp: Long = System.currentTimeMillis(),
    val isFromMe: Boolean = false,
    val isRead: Boolean = false
) : Serializable

data class Chat(
    val userId: String = "",
    val userName: String = "",
    val lastMessage: String = "",
    val lastMessageTime: Long = System.currentTimeMillis(),
    val unreadCount: Int = 0,
    val profileImage: String = ""
) : Serializable
